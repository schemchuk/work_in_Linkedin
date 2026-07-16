# work_in_Linkedin

Базова бібліотека для роботи з LinkedIn API через офіційну OAuth 2.0 інтеграцію.

## Опис

Цей репозиторій містить перевірений LinkedIn API-клієнт і інструменти для:
- авторизації через LinkedIn OAuth 2.0;
- читання базової інформації профілю;
- публікації текстових постів і постів із зображенням.

Використовується як спільна база для LinkedIn-агентів, наприклад `agtntLinSysadmin`.

## Структура

```
.
├── README.md               # Опис проєкту
├── .env.example            # Шаблон змінних середовища
├── .gitignore              # Файли, які ігнорує Git
├── requirements.txt        # Python-залежності
├── linkedin_auth.py        # Авторизація через OAuth, зберігає токен у .env
└── linkedin_client.py      # LinkedIn API клієнт
```

## Вимоги

- Python 3.10+
- Git
- Обліковий запис LinkedIn
- Додаток у [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps)

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
# відредагуй .env, встав свої Client ID та Client Secret
```

### 3. Авторизуватися

```bash
python linkedin_auth.py
```

Відкрий посилання у браузері, авторизуй додаток. Після callback у `.env` автоматично запишуться:
- `LINKEDIN_ACCESS_TOKEN`
- `LINKEDIN_PERSON_URN`

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

## Права доступу (scopes)

- `openid profile email` — базова інформація профілю та email
- `w_member_social` — публікація постів від імені користувача

## Зв’язок з іншими проєктами

Цей репозиторій може бути спільною LinkedIn-бібліотекою для агентів, наприклад `agtntLinSysadmin`.
Інші проєкти можуть імпортувати `linkedin_client.py` або використовувати ті самі змінні `LINKEDIN_ACCESS_TOKEN` та `LINKEDIN_PERSON_URN` зі свого `.env`.

## Безпека

Файли `.env` та `.linkedin_token.json` ігноруються Git і не потрапляють на GitHub. Ніколи не коміть реальні токени.

## Ліцензія

Особистий проєкт.
