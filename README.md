# work_in_Linkedin

Базова бібліотека для роботи з LinkedIn API через офіційну OAuth 2.0 інтеграцію.

## Опис

Цей репозиторій містить перевірений LinkedIn API-клієнт і інструменти для:
- авторизації через LinkedIn OAuth 2.0;
- читання базової інформації профілю;
- публікації текстових постів і постів із зображенням;
- AI-аналізу проєктів і генерації пропозицій для LinkedIn-профілю.

Використовується як спільна база для LinkedIn-агентів, наприклад `agtntLinSysadmin`.

## Структура

```
.
├── README.md               # Опис проєкту
├── .env.example            # Шаблон змінних середовища
├── .gitignore              # Файли, які ігнорує Git
├── requirements.txt        # Python-залежності
├── linkedin_auth.py        # Авторизація через OAuth, зберігає токен у .env
├── linkedin_client.py      # LinkedIn API клієнт (версіонований Posts API)
└── profile_advisor.py      # AI-аналіз GitHub-проєктів і пропозиції для профілю
```

Локально (не в git) також живуть: `.env` із секретами та `profile.md` з текстом твого LinkedIn-профілю для `profile_advisor.py`.

## Вимоги

- Python 3.10+
- Git
- Обліковий запис LinkedIn
- Додаток у [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps)
- Ключ [Claude API](https://console.anthropic.com/) (для `profile_advisor.py`)

## Швидкий старт

### 1. Створити LinkedIn додаток

1. Перейди на https://www.linkedin.com/developers/apps
2. Натисни **Create app** і заповни поля.
3. На вкладці **Auth** отримай:
   - **Client ID**
   - **Client Secret**
4. Додай Authorized redirect URL:
   ```
   http://localhost:8080/callback
   ```
5. На вкладці **Products** активуй:
   - **Sign In with LinkedIn using OpenID Connect** (для читання профілю)
   - **Share on LinkedIn** (для публікації постів)

### 2. Налаштувати середовище

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# відредагуй .env, встав свої Client ID, Client Secret та ANTHROPIC_API_KEY
```

### 3. Авторизуватися в LinkedIn

```bash
python linkedin_auth.py
```

Відкрий посилання у браузері, авторизуй додаток. Після callback у `.env` автоматично запишуться:
- `LINKEDIN_ACCESS_TOKEN`
- `LINKEDIN_PERSON_URN`

Токен LinkedIn живе ~60 днів. Коли він протухне (API поверне 401), переавторизуйся:

```bash
python linkedin_auth.py --force
```

### 4. Прочитати профіль

```bash
python linkedin_client.py
```

### 5. Опублікувати пост

```python
from linkedin_client import publish_post

publish_post("Привіт від моєї LinkedIn автоматизації! 🚀")
```

Або з зображенням:

```python
from pathlib import Path
from linkedin_client import publish_post, upload_image_to_linkedin

image_bytes = Path("image.png").read_bytes()
asset_urn = upload_image_to_linkedin(image_bytes)
publish_post("Мій пост із картинкою", asset_urn=asset_urn)
```

### 6. AI-аналіз проєктів і профілю

Створи файл `profile.md` з поточним текстом свого LinkedIn-профілю (headline, summary, skills, досвід, освіта) — він ігнорується Git і не потрапляє в репозиторій. Потім:

```bash
python profile_advisor.py
```

Скрипт:
- збере всі твої GitHub-репозиторії (публічні та приватні) через `gh CLI`;
- проаналізує їх разом із поточним LinkedIn-профілем;
- згенерує файл `profile_suggestions.md` із пропозиціями для:
  - Headline
  - About / Summary
  - Top Skills
  - Featured Projects
  - Experience
  - чернеток постів німецькою та українською

Результат — чернетки для ручного перенесення в LinkedIn. AI **не редагує профіль напряму**.

### 7. Щотижневий агент постів про проєкти

`project_agent.py` — автоматичний агент, який раз на тиждень публікує LinkedIn-пост німецькою про один з твоїх GitHub-проєктів з активністю за останні 7 днів. Логіка конвеєра (пост → промпт для картинки → DALL-E 3 → публікація з зображенням) перенесена з `agtntLinSysadmin`, але використовує версіонований Posts API з цього репозиторію.

Що робить агент:
1. Через `gh` CLI збирає репозиторії з комітами за останній тиждень.
2. Claude обирає найцікавіший проєкт (уникаючи тих, про які писали за останні 4 тижні — історія в `posted_history.json`) і пише пост німецькою. Якщо активність дрібна — тиждень пропускається.
3. Claude генерує промпт для зображення, DALL-E 3 малює картинку.
4. Пост із зображенням публікується в LinkedIn.
5. При будь-якому збої (включно з протухлим токеном LinkedIn) надсилається email.

Додаткові змінні в `.env`: `OPENAI_API_KEY`, `NOTIFY_EMAIL_TO`, `NOTIFY_EMAIL_FROM`, `NOTIFY_EMAIL_PASSWORD` (Gmail app password), опційно `GITHUB_OWNER`.

Ручний запуск:

```bash
python project_agent.py
```

Розклад через systemd user timer (п'ятниця 18:00, з `Persistent=true` — якщо машина була вимкнена, запуститься після ввімкнення):

```bash
mkdir -p ~/.config/systemd/user
cp systemd/linkedin-project-agent.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now linkedin-project-agent.timer

# щоб таймер працював без активного логіну:
loginctl enable-linger $USER

# перевірка:
systemctl --user list-timers
journalctl --user -u linkedin-project-agent.service
```

⚠️ Токен LinkedIn живе ~60 днів. Коли агент впаде з 401, прийде email — переавторизуйся: `python linkedin_auth.py --force`.

### 8. Telegram-асистент (коментарі, чернетки, аналітика)

`assistant_bot.py` — Telegram-бот, який доповнює тижневий агент:

- **Вхідні коментарі.** LinkedIn не дає читати коментарі через API (дозвіл `r_member_social` закритий для звичайних застосунків), тому бот читає **email-сповіщення LinkedIn** з Gmail через IMAP (той самий app password, що для сповіщень). Про кожен новий коментар приходить повідомлення з чернеткою відповіді німецькою (Claude): текст у tap-to-copy блоці + кнопка "Відкрити пост" + "Інший варіант".
- **Черга чернеток поста.** У середу о 18:00 таймер (`--draft`) генерує чернетку тижневого поста і шле її в Telegram: ✅ Схвалити / 🔁 Перегенерувати / ❌ Пропустити, редагування — reply'єм на повідомлення. У п'ятницю публікується те, що в черзі (якщо не чіпав — публікується як є). `/draft` генерує чернетку в будь-який момент.
- **Коментарі під чужі пости.** Надішли боту текст чужого поста — отримаєш 3 варіанти коментаря твоїм голосом німецькою.
- **Аналітика.** Всі події (коментарі, реакції, згадки) пишуться в `engagement_log.json`; `/stats` показує зведення за 30 днів.
- **Токен LinkedIn.** Дата закінчення зберігається в `.env` (`LINKEDIN_TOKEN_EXPIRES_AT`), бот щодня перевіряє і попереджає за 7 днів. Автоматичне оновлення LinkedIn дає лише партнерам Marketing Developer Platform; якщо колись з'явиться `LINKEDIN_REFRESH_TOKEN` у `.env` — `token_manager.py` почне оновлювати токен сам.

Налаштування:

```bash
# 1. Створи бота: напиши @BotFather в Telegram → /newbot → скопіюй токен
#    у .env як TELEGRAM_BOT_TOKEN

# 2. Дізнайся свій chat id:
python assistant_bot.py     # запусти і надішли боту /start — він покаже id
#    впиши його в .env як TELEGRAM_CHAT_ID і перезапусти

# 3. Постійна робота через systemd:
cp systemd/linkedin-assistant-bot.service systemd/linkedin-draft-agent.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now linkedin-assistant-bot.service
systemctl --user enable --now linkedin-draft-agent.timer

# перевірка:
systemctl --user status linkedin-assistant-bot
journalctl --user -u linkedin-assistant-bot -f
```

Моделі: за замовчуванням усе працює на `claude-sonnet-5` (дешевше); змінюється через `CLAUDE_MODEL` у `.env`.

### 9. Перевірка репозиторію перед публікацією посилання

Посилання в публічному пості — це постійне запрошення чужим людям читати репозиторій, тому `repo_safety.py` перевіряє кожен проєкт, перш ніж агент дозволить собі вставити URL:

| Перевірка | Навіщо |
|---|---|
| `visibility = PUBLIC` | приватне посилання нічого не витікає, але веде в нікуди |
| є README | по цьому посиланню прийде рекрутер — має бути що показати |
| є LICENSE | без неї формально «all rights reserved», код ніхто не може використати |
| gitleaks по **всій** історії | ключ, видалений наступним комітом, усе одно читається в історії |

URL потрапляє в дані для Claude лише тоді, коли репозиторій пройшов усі перевірки; якщо ні — модель отримує проєкт без поля `url` і пише про нього без посилань. Додатково `strip_disallowed_links()` вирізає будь-яке посилання, яке модель усе ж вигадала з назви репозиторію. Результати кешуються в `repo_safety.json` за SHA останнього коміту, тому незмінний репозиторій сканується один раз, а не щотижня.

Ручний аудит:

```bash
python repo_safety.py                    # всі публічні репозиторії
python repo_safety.py work_in_Linkedin   # конкретні
```

Потрібен [gitleaks](https://github.com/gitleaks/gitleaks) у `PATH` або в `~/.local/bin`. Якщо його нема — перевірка вважається **непройденою** (посилання не публікується), бо непідтверджений репозиторій не має отримувати лінк.

## Права доступу (scopes)

- `openid profile email` — базова інформація профілю та email
- `w_member_social` — публікація постів від імені користувача

## Зв’язок з іншими проєктами

Цей репозиторій може бути спільною LinkedIn-бібліотекою для агентів, наприклад `agtntLinSysadmin`.
Інші проєкти можуть імпортувати `linkedin_client.py` або використовувати ті самі змінні `LINKEDIN_ACCESS_TOKEN` та `LINKEDIN_PERSON_URN` зі свого `.env`.

## Версія LinkedIn API

Клієнт використовує версіонований REST API (`https://api.linkedin.com/rest/`) із заголовком `LinkedIn-Version` (зараз `202607`). LinkedIn підтримує кожну версію щонайменше рік; коли версію виведуть з експлуатації, задай новішу через змінну `LINKEDIN_VERSION` у `.env`.

## Безпека

Файли `.env`, `.linkedin_token.json`, `profile.md` та `profile_suggestions.md` ігноруються Git і не потрапляють на GitHub. Ніколи не коміть реальні токени й персональні дані.

## Ліцензія

Особистий проєкт.
