# -*- coding: utf-8 -*-
"""Подбор конкретных моделей под задачи помещений. Спецификация не изменяется."""
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
F='Arial'
H=Font(name=F,size=10,bold=True,color='FFFFFF'); HF=PatternFill('solid',fgColor='1F3864')
B=Font(name=F,size=10,bold=True); Nf=Font(name=F,size=10); LINK=Font(name=F,size=9,color='0563C1')
WARN=PatternFill('solid',fgColor='FFE699'); BAD=PatternFill('solid',fgColor='FFC7CE'); OK=PatternFill('solid',fgColor='E2EFDA')
thin=Side(style='thin',color='BFBFBF'); BRD=Border(left=thin,right=thin,top=thin,bottom=thin)
M='#,##0 ₽;[Red](#,##0);-'

ROWS=[
 ('Моноблок — базовое рабочее место', 29,
  'Охрана и СБ (105.1, 107, 118, 119) — 7 · Кассы (109, 109.1) — 2 · Вестибюль/ресепшн (102) — 2 · '
  'Медицина (141, 146) — 2 · Судьи (134.2) — 1 · Распечатка результатов (162.2) — 2 · '
  'Админблок (178–182) — 5 · Техперсонал (183) — 3 · Тренеры (184) — 4 · Операторская (187.2) — 1',
  'Документы, 1С, почта, браузер, клиент СКУД, печать. Ничего тяжёлого.',
  'DEXP Aquilon AQ31HE 23.8"', 'Intel N100, 8 ГБ, SSD 256 ГБ, IPS Full HD, Windows 11 Pro', 29799,
  'https://www.dns-shop.ru/product/aad9638ba8bcea04/238-monoblok-dexp-aquilon-aq31he/',
  'Windows 11 Pro уже в комплекте — отдельно ОС покупать не надо. Клавиатура и мышь входят.'),

 ('Моноблок — фото и трансляция', 6,
  'Фотографы (199.2, 199.3) — 4 · Комментаторская студия (193) — 2',
  'Обработка фотосъёмки, работа с видеопотоком. Здесь базовой машины мало.',
  'DEXP Aquilon AQ33HE 23.8" — минимум', 'Intel N100, 16 ГБ, SSD 512 ГБ, IPS Full HD, Windows 11 Pro', 37499,
  'https://www.dns-shop.ru/product/b3496feba1796483/238-monoblok-dexp-aquilon-aq33he/',
  'ЧЕСТНО: у AQ33HE больше памяти и диска, но процессор тот же слабый N100. Для Lightroom/Photoshop '
  'он будет тормозить. Если фотографы реально обрабатывают RAW — берите класс Core i5 / Ryzen 5, '
  'это 55–75 тыс. Каталог: https://www.dns-shop.ru/catalog/recipe/31262d1866ae2669/dla-ofisa/'),

 ('МФУ монохромное А4 — вместо цветного А3', 11,
  'Охрана и СБ (105.1, 107, 118, 119) — 4 · Вестибюль (102) — 1 · Распечатка (162.2) — 1 · '
  'Кабинеты (178, 181, 182) — 3 · Техперсонал (183) — 1 · Комментаторская (193) — 1',
  'Пропуска, акты, служебки, распечатка списков. Цвет и А3 здесь не нужны.',
  'Pantum M6500', 'ч/б лазерное 3-в-1, А4, 22 стр/мин, нагрузка 20 000 стр/мес', 15578,
  'https://www.officemag.ru/catalog/goods/353812/',
  'ГЛАВНАЯ ЭКОНОМИЯ. В спецификации на все 13 точек заложено цветное лазерное А3 — это от 200 000 ₽ '
  'за штуку. Охране и кассе такое не нужно. Замена 11 из 13 на монохром А4 экономит ~2,0 млн ₽.'),

 ('МФУ цветное лазерное А3 — там, где правда нужно', 2,
  'Приёмная (179) — 1 · Кабинет директора (180) — 1',
  'Афиши, схемы, презентации, цветные документы на подпись.',
  'Kyocera ECOSYS M8124cidn', 'цветное лазерное МФУ А3, от 201 987 ₽', 201987,
  'https://market.yandex.ru/category/tsvetnyye-lazernyye-mfu-dlya-ofisa-a3',
  'Оставить только в администрации. Каталоги: https://www.dns-shop.ru/catalog/recipe/38b13e633dec8dd4/cvetnye-a3/ '
  'и https://www.citilink.ru/catalog/mfu--mfu-lazernye-a3/'),

 ('МФУ с фотопечатью А3', 3,
  'Помещения фотографа (199.2, 199.3)',
  'Печать фотографий, в т.ч. А3. В спецификации так и написано — «с возможностью печати фотографий».',
  'Epson L11050', 'струйное А3+, СНПЧ (без картриджей), Wi-Fi, ~20 коп. за лист А4', 50416,
  'https://www.marketvale.ru/product/c11ck39505-c11ck39503-printer-fabrika-pechati-epson-l11050',
  'СНПЧ принципиально: на фотопечати картриджи разорят. Расходники дешевле в разы.'),

 ('Принтер А4 — билеты', 2,
  'Касса массового катания (109) · Помещение кассы (109.1)',
  'Печать билетов и чеков-корешков. Скорость и цвет не нужны, нужна надёжность и дешёвый тонер.',
  'Pantum P2500W', 'ч/б лазерный, А4, 22 стр/мин, Wi-Fi, нагрузка 15 000 стр/мес', 9967,
  'https://www.foroffice.ru/products/description/169837.html',
  'Есть версия без Wi-Fi (P2500) — практически те же деньги. Ситилинк: '
  'https://www.citilink.ru/product/printer-lazernyi-pantum-p2500w-lazernyi-cvet-chernyi-934242/'),

 ('Ноутбук', 1,
  'Зал универсальный, гимнастики, аэробики ОФП (163)',
  'Музыка на занятиях, показ упражнений, расписание. Нагрузки никакой.',
  'Офисный 15.6", i3/Ryzen 3', '8 ГБ, SSD 512 ГБ, Full HD IPS', 40000,
  'https://www.dns-shop.ru/catalog/recipe/5c830f6aca343a75/dla-ofisa/',
  'Конкретную модель не фиксирую — в этом классе они меняются каждый квартал. Берите любой из '
  'каталога в диапазоне 35–50 тыс. Ситилинк: https://www.citilink.ru/catalog/noutbuki--deshevyie-noutbuki/'),

 ('Телевизор 43" на консоли', 8,
  'Кабинеты 178–184 — 7 · Комната отдыха техперсонала (197.4) — 1',
  'Информационный экран, совещания, новости. Смотрят с 2–3 метров.',
  'DEXP F431 43"', 'Full HD 1920×1080, Direct LED, Wi-Fi, Smart TV, 3×HDMI', 15799,
  'https://www.dns-shop.ru/product/787468582d2aed20/43-109-sm-led-televizor-dexp-f431-seryj/',
  'ВАЖНО: в спецификации «телевизор на консоли», но сама консоль (кронштейн) отдельной строкой '
  'не заложена. Заложить ~2 000 ₽ на кронштейн под каждый — это ещё 16 тыс.'),
]

wb=openpyxl.Workbook(); ws=wb.active; ws.title='Подбор под задачи'
ws['A1']='ПОДБОР КОМПЬЮТЕРНОЙ ТЕХНИКИ ПОД ЗАДАЧИ ПОМЕЩЕНИЙ'
ws['A1'].font=Font(name=F,size=14,bold=True)
ws['A2']='Модели подобраны по тому, кто и чем занят в помещении. Уровень — рабочий, не премиум. Цены — открытые прайсы, август 2026, не КП. Спецификация не изменялась.'
ws['A2'].font=Font(name=F,size=9,italic=True)
cols=['Позиция','Кол-во','Где стоит','Чем там занимаются','Что брать','Конфигурация','Цена за шт, ₽','Сумма, ₽','Ссылка','Комментарий']
widths=[32,8,52,44,30,42,13,15,58,66]
r=4
for j,(c,w) in enumerate(zip(cols,widths),1):
    cell=ws.cell(row=r,column=j,value=c); cell.font=H; cell.fill=HF; cell.border=BRD
    cell.alignment=Alignment(wrap_text=True,vertical='center',horizontal='center')
    ws.column_dimensions[get_column_letter(j)].width=w
ws.row_dimensions[r].height=32; ws.freeze_panes=ws.cell(row=r+1,column=1)
r+=1; first=r
for name,qty,where,task,model,conf,price,link,note in ROWS:
    ws.cell(row=r,column=1,value=name).font=B
    ws.cell(row=r,column=2,value=qty)
    ws.cell(row=r,column=3,value=where)
    ws.cell(row=r,column=4,value=task)
    ws.cell(row=r,column=5,value=model).font=B
    ws.cell(row=r,column=6,value=conf)
    ws.cell(row=r,column=7,value=price)
    ws.cell(row=r,column=8,value='=B%d*G%d'%(r,r))
    ws.cell(row=r,column=9,value=link).font=LINK
    ws.cell(row=r,column=10,value=note)
    if 'ЭКОНОМИЯ' in note: ws.cell(row=r,column=10).fill=OK
    elif 'ЧЕСТНО' in note or 'ВАЖНО' in note: ws.cell(row=r,column=10).fill=BAD
    else: ws.cell(row=r,column=10).fill=WARN
    r+=1
last=r-1
ws.cell(row=r,column=1,value='ИТОГО').font=B
ws.cell(row=r,column=2,value='=SUM(B%d:B%d)'%(first,last)).font=B
ws.cell(row=r,column=8,value='=SUM(H%d:H%d)'%(first,last)).font=B
tot=r
r+=2
ws.cell(row=r,column=1,value='СРАВНЕНИЕ ПО МФУ').font=Font(name=F,size=12,bold=True); r+=1
comp=[('Если брать как записано в спецификации: 13 × цветное лазерное А3', 13, 201987),
      ('Предложение: 11 × монохром А4 (Pantum M6500)', 11, 15578),
      ('плюс 2 × цветное А3 в администрацию', 2, 201987)]
cs=r
for t,q,p in comp:
    ws.cell(row=r,column=1,value=t); ws.cell(row=r,column=2,value=q); ws.cell(row=r,column=7,value=p)
    ws.cell(row=r,column=8,value='=B%d*G%d'%(r,r)); r+=1
ws.cell(row=r,column=1,value='ЭКОНОМИЯ').font=B
ws.cell(row=r,column=8,value='=H%d-H%d-H%d'%(cs,cs+1,cs+2)).font=B
ws.cell(row=r,column=8).fill=OK
for row in ws.iter_rows(min_row=first,max_row=r,min_col=1,max_col=10):
    for c in row:
        if not c.font.bold and c.font.color is None: c.font=Nf
        c.border=BRD; c.alignment=Alignment(wrap_text=(c.column in (1,3,4,5,6,9,10)),vertical='top')
        if c.column in (7,8): c.number_format=M
wb.save('ПК_подбор_под_задачи.xlsx')
# контрольный расчёт
tot_sum=sum(q*p for _,q,_,_,_,_,p,_,_ in ROWS)
print('позиций:', len(ROWS), '| единиц:', sum(q for _,q,_,_,_,_,_,_,_ in ROWS))
print('ИТОГО:', format(tot_sum,',').replace(',',' '), '₽')
print('МФУ как в спец.:', format(13*201987,',').replace(',',' '),
      '| предложение:', format(11*15578+2*201987,',').replace(',',' '),
      '| экономия:', format(13*201987-11*15578-2*201987,',').replace(',',' '), '₽')
