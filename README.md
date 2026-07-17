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
