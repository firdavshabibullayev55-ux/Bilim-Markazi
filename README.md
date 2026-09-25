# Bilim Markazi Bot — AI mode + AI-free Katta Quiz

## Katta Quiz

Katta Quiz endi AI API ishlatmaydi. Bot ichidagi `quiz_bank.py` faylida 1000 ta tayyor savol mavjud.

- Boshlang‘ich baza: 1000 ta savol.
- Admin navbatdagi quiz uchun 40 / 60 / 80 / 100 ta savol tanlaydi.
- Savollar qolgan bazadan tasodifiy olinadi.
- Quiz yakunlangach aynan ishlatilgan savollar bazadan o‘chiriladi: masalan 1000 → 960.
- Keyingi quiz faqat qolgan savollardan foydalanadi.
- To‘liq tugatilmagan yoki tugatilgan ishtirokchi qayta kirib quizni noldan boshlay olmaydi.
- Qayta kirishga urinsa: “✅ Siz bu quizda ishtirok etgansiz. Natijangizni kuting.”
- Javoblar SQLite’da `competition_answers` orqali bir martalik saqlanadi.
- Quiz tugaguncha ishtirokchiga reyting ko‘rsatilmaydi.
- Quiz tugagach adminlarga to‘liq reyting yuboriladi; ishtirokchiga faqat o‘z natijasi yuboriladi.
- Kundalik scheduler 21:00 da ishlaydi.
- `quiz_bank.py`da variantlar va javob pozitsiyalari boshlang‘ich yuklashda A/B/C/D bo‘yicha muvozanatlashtiriladi.

## AI rejimi

🤖 AI yordamchi alohida ishlashda davom etadi. `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` sozlamalari AI yordamchi, darslar va boshqa AI funksiyalari uchun ishlatiladi. Katta Quiz uchun AI chaqirilmaydi.

## Muhim

1000 ta savolning strukturasi, takrorlanmasligi va javob formatlari avtomatik validator bilan tekshiriladi. Factual content bo‘yicha mutlaq “xatosiz” kafolat berishning iloji yo‘q; shuning uchun botga joylashdan oldin bankni qo‘shimcha ekspert tekshiruvidan o‘tkazish maqsadga muvofiq.

# Bilim Markazi Bot — V4.3 FULL FIX

Bu versiya foydalanuvchi yuborgan V4.2 ZIP asosida tozalandi va asosiy muammolar qayta tekshirildi.

## Tuzatilgan muammolar

- `user_subjects` uchun SQLite `near "` sintaksis xatosi olib tashlandi. Fan/sinf saqlash `DELETE + INSERT` tranzaksiyasi bilan bajariladi.
- `pypdf` requirements ichida bor va `check_setup.py` uning o‘rnatilganini tekshiradi.
- PDF darslik tanlanganda bot ochiq PDF/web manbani yuklab, PDF matnini o‘qishga harakat qiladi.
- Qidiruv natijalarida `📄 Ochish` tugmasi bor — PDF/manbani foydalanuvchi bevosita ochishi mumkin.
- Tanlangan darslikni keyingi qidiruv o‘chirib yubormaydi; shu sababli `user_books` buzilib qolmaydi.
- `📚 Fanlar` → sinf → darslik → dars oqimi saqlandi.
- `🔙 Fanlar` bosilganda eski reply-keyboard qayta chiqib ketmaydi.
- AI, Internet qidiruvi va Admin panelga kirishda eski reply-keyboard yashiriladi.
- AI/qidiruv/support ichida `🔙 Asosiy menyu` inline tugmasi bor.
- AI tarix/xotirasi SQLite'da saqlanadi: bot restart bo‘lsa ham oxirgi suhbatdagi xabarlar qoladi.
- AI keyingi savolda oldingi suhbatning oxirgi xabarlarini modelga qayta yuboradi.
- AI chat `updated_at` bilan yangilanadi va tarix oxirgi faol suhbat bo‘yicha tartiblanadi.
- Dars yaratishda ichki exception foydalanuvchiga ko‘rsatilmaydi.
- SQLite DB papkasi avtomatik yaratiladi; Render'da `/var/data/education.db` ishlatish mumkin.
- 20:00 scheduler DB orqali duplicate musobaqani oldini oladi; bot 20:00 dan keyin qayta ishga tushsa, o‘sha kun uchun musobaqani catch-up qilib yaratishi mumkin.
- Quiz sessiyasi DB'da saqlanadi va bir ishtirokchi tugatgani butun musobaqani yopmaydi.

## PDF bo‘yicha muhim

- Bot faqat ochiq va ruxsat etilgan manbalarni ishlatadi.
- Login, paywall yoki DRM chetlab o‘tilmaydi.
- Skan qilingan PDFda matn qatlami bo‘lmasa, `pypdf` matn chiqarmasligi mumkin. Bunday fayl uchun OCR kerak bo‘ladi.
- Internet qidiruvi topgan har bir fayl rasmiy darslik ekaniga avtomatik kafolat berilmaydi; kerak bo‘lsa rasmiy manbani tanlang.

## Windows'da ishga tushirish

1. ZIP'ni to‘liq papkaga chiqarib oling.
2. `.env.example` nusxasini `.env` nomiga o‘zgartiring.
3. `.env` ichiga `BOT_TOKEN`, `ADMIN_IDS`, `CHANNEL_ID`, `CHANNEL_URL` va `AI_API_KEY` kiriting.
4. Eng oson yo‘l: `install_and_run.bat` ni ishga tushiring.
5. Yoki terminalda:

```powershell
python -m pip install -r requirements.txt
python check_setup.py
python bot.py
```

Agar `Import "pypdf" could not be resolved` chiqsa, VS Code ishlatayotgan Python interpreterga aynan shu buyruq bilan o‘rnating:

```powershell
python -m pip install pypdf
```

Agar `python` ishlamasa:

```powershell
py -m pip install pypdf
```

## Eski xato haqida

Agar terminalda yana eski:

`sqlite3.OperationalError: near "": syntax error`

xatosi chiqsa, botni boshqa eski `bot.py`dan ishga tushirayotgan bo‘lishingiz mumkin. VS Code terminalida quyidagini bajaring:

```powershell
python -c "import os; print(os.path.abspath('bot.py'))"
```

U ko‘rsatgan fayl aynan shu ZIP ichidagi yangi `bot.py` bo‘lishi kerak.

## Render

- `DB_PATH=/var/data/education.db` va persistent disk ishlating.
- UptimeRobot faqat health/ping uchun bo‘lishi mumkin; 20:00 quiz scheduler botning o‘zida.
- Bir nechta Render instance bilan SQLite ishlatish tavsiya etilmaydi. Katta yuklama uchun PostgreSQL + Redis/task queue kerak.


## UptimeRobot / Health

Bot Render'da `/health` endpointini beradi. UptimeRobot uchun: `https://SIZNING-RENDER-URL.onrender.com/health`. Javob `200 OK` va `OK`. Render health check ham `/health` ga sozlangan.



## OpenAI API

Render Environment'da:

```env
AI_API_KEY=YOUR_OPENAI_API_KEY
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini
```

API keyni kodga yozmang. `AI_API_KEY` faqat Render Environment orqali beriladi.
