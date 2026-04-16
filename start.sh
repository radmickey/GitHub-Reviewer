#!/bin/bash

ORG="is-itmo-c-25"
WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET:-$(grep GITHUB_WEBHOOK_SECRET .env | cut -d= -f2)}"

# Запускаем uvicorn в фоне
echo "🚀 Запускаем сервер..."
uvicorn main:app --port 8000 &
UVICORN_PID=$!

# Запускаем cloudflared и пишем лог во временный файл
echo "🌐 Запускаем cloudflared..."
cloudflared tunnel --url http://localhost:8000 2>&1 | tee /tmp/cloudflared.log &
CLOUDFLARED_PID=$!

# Ждём пока появится URL (до 30 секунд)
echo "⏳ Ждём URL..."
for i in $(seq 1 30); do
    URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/cloudflared.log | head -1)
    if [ -n "$URL" ]; then
        break
    fi
    sleep 1
done

if [ -z "$URL" ]; then
    echo "❌ Не удалось получить URL от cloudflared"
    kill $UVICORN_PID $CLOUDFLARED_PID
    exit 1
fi

echo "✅ URL: $URL"

# Ищем или создаём webhook (ID сохраняется в .hook_id между запусками)
echo "🔗 Проверяем webhook в GitHub..."
HOOK_ID_FILE=".hook_id"
HOOK_ID=""

if [ -f "$HOOK_ID_FILE" ]; then
    SAVED_ID=$(cat "$HOOK_ID_FILE")
    # Проверяем что хук с таким ID ещё существует
    if gh api orgs/$ORG/hooks/$SAVED_ID --silent 2>/dev/null; then
        HOOK_ID=$SAVED_ID
        echo "✅ Найден webhook #$HOOK_ID"
    else
        echo "⚠️  Webhook #$SAVED_ID не найден, создадим новый"
        rm -f "$HOOK_ID_FILE"
    fi
fi

if [ -n "$HOOK_ID" ]; then
    echo "🔄 Обновляем URL..."
    gh api orgs/$ORG/hooks/$HOOK_ID \
        -X PATCH \
        -f "config[url]=$URL/webhook" \
        -f "config[content_type]=json" \
        -f "config[secret]=$WEBHOOK_SECRET" \
        --silent && echo "✅ Webhook обновлён" || echo "❌ Не удалось обновить webhook"
else
    echo "➕ Создаём webhook..."
    NEW_ID=$(gh api orgs/$ORG/hooks \
        -X POST \
        -f "name=web" \
        -f "config[url]=$URL/webhook" \
        -f "config[content_type]=json" \
        -f "config[secret]=$WEBHOOK_SECRET" \
        -f "events[]=issue_comment" \
        -f "events[]=pull_request_review_comment" \
        -F "active=true" \
        --jq ".id" 2>/dev/null)
    if [ -n "$NEW_ID" ]; then
        echo "$NEW_ID" > "$HOOK_ID_FILE"
        echo "✅ Webhook создан (#$NEW_ID), ID сохранён в $HOOK_ID_FILE"
    else
        echo "❌ Не удалось создать webhook"
    fi
fi

echo ""
echo "🤖 Бот запущен! Пиши @review в любом PR организации $ORG"
echo "   Нажми Ctrl+C чтобы остановить"

# Ждём Ctrl+C
trap "echo ''; echo 'Останавливаем...'; kill $UVICORN_PID $CLOUDFLARED_PID; exit 0" INT
wait
