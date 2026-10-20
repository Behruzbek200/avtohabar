# Tpublish Pro

Guruhlarga avtomatik xabar yuboruvchi Telegram bot.  
**7 tarif** · **HUMO avto-toʻlov** · **Multi-akkaunt** · **Pastki reklama** · **Referal pul** · **Render + Supabase**

## Asosiy imkoniyatlar

### Foydalanuvchi
- Bir nechta akkaunt (faqat raqam + kod + 2FA, API soʻralmaydi)
- Guruhlar: akkauntdan yuklash, ✅ bilan tanlash
- Matn / rasm / video, {nom} {sana} {vaqt}
- Interval (soniya yoki 5m) + ish vaqti
- Boshlash / Toʻxtatish
- Statistika, kunlik limit
- 7 tarif (Free → VIP)
- HUMO noyob summa + avto-tasdiqlash
- Referal: doʻst kelganda pul + toʻlovdan %
- Balans
- Pastki reklama (tarif boʻyicha, bot username avtomatik)
- 3 kunlik sinov, eslatmalar, ticket

### Admin
- Dashboard, foydalanuvchilar, tariflar
- HUMO ulash (nomer + kod + 2FA)
- Pastki reklama: yoqish/oʻchirish, matn, qaysi tariflarda
- Toʻlovlar, promokod, ommaviy xabar, ticketlar

## Tariflar (default)

| Tarif | Narx | Akkaunt | Guruh | Kuniga |
|-------|------|---------|-------|--------|
| Free | 0 | 1 | 2 | 10 |
| Start | 29 000 | 1 | 10 | 50 |
| Basic | 49 000 | 1 | 25 | 100 |
| Standard | 79 000 | 2 | 50 | 250 |
| Pro | 119 000 | 3 | 100 | 500 |
| Premium | 179 000 | 5 | 200 | 1 000 |
| VIP | 299 000 | 10 | cheksiz | 3 000 |

## Pastki reklama

- Yoqilgan tariflarda xabar tagiga avtomatik qoʻyiladi
- Matn boʻsh boʻlsa: `🤖 @BotUsername orqali yuborildi` (bot oʻzi oladi)
- Admin: Pastki reklama → tariflarni ✅/⬜ belgilash
- Default: free, start, basic da chiqadi

## Oʻrnatish (Render)

1. Supabase → DATABASE_URL
2. my.telegram.org → API_ID, API_HASH
3. ENCRYPT_KEY:
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
4. BotFather → BOT_TOKEN
5. Render Web Service:
   - Build: pip install -r requirements.txt
   - Start: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120
   - Env: BOT_TOKEN, ADMIN_IDS, DATABASE_URL, API_ID, API_HASH, ENCRYPT_KEY, WEBHOOK_URL
6. Deploy → /admin → HUMO ulash

## Mahalliy

cp .env.example .env
pip install -r requirements.txt
python app.py

## Xavfsizlik

- Session Fernet bilan shifrlanadi
- API ID/Hash foydalanuvchiga soʻralmaydi
- HUMO sessiya bazada

MIT License
