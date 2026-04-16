# Claude GitHub Reviewer

ИИ-ревьювер лабораторных работ. Висит как webhook на GitHub-организации: когда ты пишешь `@review` в любом PR — бот анализирует diff, читает README задания и оставляет подробный отчёт для преподавателя.

## Как это работает

1. Ты пишешь `@review` (можно с доп. инструкцией) в комментарии к PR
2. Webhook получает событие, проверяет что ты — `ALLOWED_USER`
3. Бот параллельно скачивает diff PR и README репозитория
4. Фильтрует бинарники, артефакты сборки и IDE-файлы
5. Отправляет в LLM с промптом преподавателя C++
6. Постит отчёт обратно в PR

## Установка

```bash
pip install -r requirements.txt
```

Также нужны:
- [`gh`](https://cli.github.com/) — для получения GitHub токена через `gh auth login`
- [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) — для временного публичного туннеля

## Конфигурация

Создай `.env` в корне проекта:

```bash
# GitHub
GITHUB_WEBHOOK_SECRET=your_webhook_secret
ALLOWED_USERS=user1,user2            # кто может триггерить ревью (через запятую)
# GITHUB_TOKEN=ghp_...               # опционально, иначе берётся через gh auth token

# Провайдер LLM
LLM_PROVIDER=anthropic               # или openai

# --- Anthropic ---
ANTHROPIC_API_KEY=sk-ant-...
# CLAUDE_MODEL=claude-sonnet-4-6     # опционально, это дефолт
# ANTHROPIC_BASE_URL=...             # опционально
# ANTHROPIC_AUTH_TOKEN=...           # опционально, альтернатива API key

# --- OpenAI / совместимые (Groq, Together, LocalAI...) ---
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o                # опционально, это дефолт
# OPENAI_BASE_URL=https://api.groq.com/openai  # опционально
```

## Запуск

```bash
./start.sh
```

Скрипт:
1. Поднимает `uvicorn` на порту 8000
2. Запускает `cloudflared` и ждёт публичный URL
3. Обновляет webhook в GitHub-организации через `gh api`

Перед первым запуском в `start.sh` замени:
```bash
ORG="is-itmo-c-25"     # название организации на GitHub
HOOK_ID="604421264"    # ID webhook'а (см. ниже)
```

### Как получить HOOK_ID

```bash
gh api orgs/<ORG>/hooks
```

Найди нужный webhook в выводе и скопируй `id`.

## Использование

В любом PR организации напиши комментарий:

```
@review
```

Или с дополнительной инструкцией:

```
@review проверь особенно внимательно работу с памятью и утечки
```

Бот ответит отчётом со структурой:
- ✅ Что сделано хорошо
- ❌ Проблемы и ошибки (критические / некритические)
- 👀 На что обратить внимание на защите
- 🎓 Вопросы для защиты (из теоретического минимума в README)
- 📊 Общая оценка

## Примеры провайдеров

| Провайдер | LLM_PROVIDER | Переменные |
|-----------|-------------|------------|
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` |
| OpenAI | `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| Groq | `openai` | `OPENAI_API_KEY`, `OPENAI_BASE_URL=https://api.groq.com/openai`, `OPENAI_MODEL=llama-3.3-70b-versatile` |
| Together | `openai` | `OPENAI_API_KEY`, `OPENAI_BASE_URL=https://api.together.xyz`, `OPENAI_MODEL=...` |
