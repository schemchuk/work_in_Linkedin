# work_in_Linkedin

Проєкт для автоматизації роботи з профілем LinkedIn через офіційне API.

## Опис

Цей репозиторій містить інструменти та скрипти для:
- авторизації через LinkedIn OAuth 2.0;
- читання даних профілю;
- публікації постів (за наявності відповідних прав).

## Структура

```
.
├── README.md               # Опис проєкту
├── .env.example            # Шаблон змінних середовища
├── .gitignore              # Файли, які ігнорує Git
├── requirements.txt        # Python-залежності
├── linkedin_auth.py        # Авторизація через OAuth
└── linkedin_client.py      # Приклади запитів до LinkedIn API
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
   - **Share on LinkedIn** (якщо плануєш публікувати пости)

### 2. Налаштувати середовище

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Скопіюй `.env.example` у `.env` і заповни своїми значеннями:

```bash
cp .env.example .env
```

### 3. Авторизуватися

```bash
python linkedin_auth.py
```

Відкрий посилання у браузері, авторизуй додаток. Токен збережеться у файл `.linkedin_token.json`.

### 4. Прочитати профіль

```bash
python linkedin_client.py
```

## Права доступу (scopes)

- `openid profile email` — базова інформація профілю та email
- `w_member_social` — публікація постів від імені користувача

## Ліцензія

Особистий проєкт.
