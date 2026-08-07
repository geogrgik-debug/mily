# -*- coding: utf-8 -*-
"""ИТОГОВЫЙ список закупки ПК-оборудования — с учётом снятых с производства
и отсутствующих позиций. Серверы DEPO исключены (заказаны отдельно)."""
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

F='Arial'
H=Font(name=F,size=10,bold=True,color='FFFFFF'); HF=PatternFill('solid',fgColor='1F3864')
B=Font(name=F,size=10,bold=True); Nf=Font(name=F,size=10); LINK=Font(name=F,size=9,color='0563C1')
WARN=PatternFill('solid',fgColor='FFE699'); BAD=PatternFill('solid',fgColor='FFC7CE'); OKF=PatternFill('solid',fgColor='E2EFDA')
thin=Side(style='thin',color='BFBFBF'); BRD=Border(left=thin,right=thin,top=thin,bottom=thin)
M='#,##0 ₽;[Red](#,##0);-'

# позиция, строки спец., кол-во, модель, цена, ссылки, статус, комментарий
ROWS=[
('Моноблок — базовое рабочее место','2500, 2516, 2537, 2558, 2570, 2610, 2622, 2967, 3015, 3134, 3249, 3442, 3455, 3472, 3485, 3499, 3513, 3520, 3674',
 29,'DEXP Aquilon AQ31HE 23.8" — N100, 8 ГБ, SSD 256, Win 11 Pro',29799,
 'https://www.dns-shop.ru/product/aad9638ba8bcea04/238-monoblok-dexp-aquilon-aq31he/','',
 'Охрана, кассы, вестибюль, медпункт, админблок, техперсонал, тренеры. Клавиатура и мышь в комплекте.'),

('Моноблок — фото и трансляция','3775, 3853, 3861',6,
 'DEXP Aquilon AQ33HE 23.8" — N100, 16 ГБ, SSD 512, Win 11 Pro',37499,
 'https://www.dns-shop.ru/product/b3496feba1796483/238-monoblok-dexp-aquilon-aq33he/','ПРОВЕРИТЬ',
 'Процессор тот же слабый N100. Если фотографы обрабатывают RAW — нужен класс i5/Ryzen 5, это +100–200 тыс. на 6 машин.'),

('МФУ монохромное А4','2501, 2517, 2538, 2611, 2623, 3250, 3441, 3484, 3498, 3512, 3776',11,
 'Pantum M6500 — ч/б лазерное 3-в-1, А4, 22 стр/мин',15578,
 'https://www.officemag.ru/catalog/goods/353812/','ЭКОНОМИЯ',
 'В спецификации на эти точки заложено цветное лазерное А3 по 201 987 ₽. Охране и кассе оно не нужно. Замена 11 точек экономит 2 050 499 ₽.'),

('МФУ цветное лазерное А3','3454, 3471',2,
 'Kyocera ECOSYS M8124cidn или аналог',201987,
 'https://market.yandex.ru/category/tsvetnyye-lazernyye-mfu-dlya-ofisa-a3','',
 'Оставлено только в приёмной и кабинете директора — афиши, схемы, презентации.'),

('МФУ с фотопечатью А3','3854, 3862',3,
 'Epson L11050 — струйное А3+, СНПЧ',50416,
 'https://www.marketvale.ru/product/c11ck39505-c11ck39503-printer-fabrika-pechati-epson-l11050','',
 'СНПЧ обязательно: на фотопечати картриджи разорят. ~20 коп. за лист А4.'),

('Принтер А4 — билеты','2560, 2572',2,
 'Pantum P2500W — ч/б лазерный, А4',9967,
 'https://www.foroffice.ru/products/description/169837.html','',
 'Кассы. Печать билетов.'),

('Принтер лазерный А4','2322',1,
 'Kyocera ECOSYS PA4500x — 45 стр/мин, ресурс 150 000 стр/мес',44200,
 'https://www.dns-shop.ru/product/53066909103df2c6/printer-lazernyj-kyocera-pa4500x/','ЗАМЕНА',
 'В спецификации ECOSYS P3045dn — снят с производства. PA4500x его прямой преемник: тот же формат и скорость.'),

('Ноутбук','3278',1,
 'Acer Aspire Lite AL15-61P / ASUS Vivobook Go 15 / Acer Extensa 15 — 15.6", IPS FHD, 16 ГБ, SSD 512',40000,
 'https://www.citilink.ru/catalog/noutbuki--noutbuki-do-40-000/','НАЛИЧИЕ',
 'Зал ОФП, музыка на занятиях. Конкретную модель не фиксирую — наличие в этом классе скачет. Брать любой до 45 тыс. по указанным требованиям.'),

('Телевизор 43"','3443, 3452, 3469, 3483, 3497, 3511, 3519, 3839',8,
 'DEXP F431 43" — Full HD, Direct LED, Smart TV',15799,
 'https://www.dns-shop.ru/product/787468582d2aed20/43-109-sm-led-televizor-dexp-f431-seryj/','',
 'Кабинеты 178–184 и комната отдыха техперсонала.'),

('Кронштейны под телевизоры','нет в спецификации',8,
 'Настенный кронштейн под 43"',2000,
 'https://market.yandex.ru/category/kronshteyny-dlya-televizorov','ДОБАВЛЕНО',
 'В спецификации написано «телевизор на консоли», но сама консоль отдельной строкой не заложена.'),

('Монитор 27" для АРМ видеонаблюдения','1989',8,
 'Hikvision DS-D5027FN01 — 27", 1920×1080, 24/7',13673,
 'https://www.nix.ru/autocatalog/lcd_hikvision/27-ZHK-monitor-Hikvision-DS-D5027FN01-LCD-1920x1080-D-Sub-HDMI_704844.html','ЗАМЕНА',
 'Артикул DS-D5027FN сменился на FN01 — это тот же монитор. Запасные точки: Тинко, Регард, Минимакс, DSSL. Если нет — DS-D5027UC (4K). Бытовой монитор не брать: экран работает круглосуточно.'),

('Монитор 31.5"','1987',6,
 'Hikvision DS-D5032QE — 31.5", 1920×1080, 24/7',25439,
 'https://www.telecamera.ru/catalog/Domofony/Videodomofony/Monitory/HIKVISION/DS_D5032QE.htm','',''),

('Монитор 21.5"','2047',2,
 'Hikvision DS-D5022QE-B — 21.5", 1920×1080',17853,
 'https://hikvision24.ru/aksessuary/monitory/monitor-ds-d5022qe-b/','',''),

('Монитор 23.8"','2321',2,
 'Xiaomi G24i 2026 / AOC 24B36X / MSI PRO MP243X — 23.8", IPS, FHD, HDMI+DP',11999,
 'https://www.dns-shop.ru/catalog/recipe/9d63f2d6341c1b88/24/','ЗАМЕНА',
 'В спецификации Dell P2419H. Dell ушёл из России, модель 2019 года, в наличии нет.'),

('Кронштейн для мониторов','1988',6,
 'Kromax TECHNO-11 BLACK',2500,
 'https://www.kns.ru/product/kronshtein-kromax-techno-11-black/','ПРОВЕРИТЬ',
 'Рассчитан на 10–32" и до 15 кг. Мониторы DS-D5032QE — 31.5", на самой границе. Проверить вес.'),

('Жёсткий диск 14 ТБ','1926',10,
 'WD Purple Pro WD141PURP — под видеонаблюдение',50618,
 'https://www.dns-shop.ru/product/db9b6068eb79ed20/14-tb-zestkij-disk-wd-purple-pro-wd141purp/','',
 'Есть более новый WD142PURP — уточнить, чем комплектовать.'),

('Проектор','3665',1,
 'Acer X1328WHn',54890,
 'https://www.regard.ru/product/710326/proektor-acer-x1328whn','',
 'Зал для пресс-конференций / методический кабинет.'),

('Карт-принтер','2146',1,
 'Evolis Zenius 2 Classic — односторонняя печать',130452,
 'https://spb.xcom-shop.ru/evolis_zenius_2_classic_1320687.html','ЭКОНОМИЯ',
 'В спецификации Fargo C50 — снят. Прямой преемник Fargo DTC1250e стоит 335 894 ₽. Он не нужен: карты по спецификации идут уже номерные с чипом Em Marine (стр. 2149), кодировать нечего, принтер только печатает. Evolis дешевле на 205 442 ₽.'),

('Карты пластиковые','2149',500,
 'EM-Marine TK4100 ISO, 125 кГц, номерные',14,
 'https://zkteco-store.ru/shop/rfid-karta-em-marine-iso-125-kgc-s-nomerom/','',
 'Продаются упаковками по 200 шт.'),

('ИБП 19" 2U','1699',1,
 'SKAT-UPS 1000 RACK — 1 кВА / 0,9 кВт',71760,
 'https://satro-paladin.com/catalog/product/11230/','',''),
]

wb=openpyxl.Workbook(); ws=wb.active; ws.title='Итоговый список'
ws['A1']='ЗАКУПКА КОМПЬЮТЕРНОГО ОБОРУДОВАНИЯ — ИТОГОВЫЙ СПИСОК'
ws['A1'].font=Font(name=F,size=14,bold=True)
ws['A2']='Серверы DEPO Storm 1420Q1 и 3450Z1 в список не входят — заказаны отдельно. Цены — открытые прайсы, август 2026, не коммерческие предложения. Спецификация не изменялась.'
ws['A2'].font=Font(name=F,size=9,italic=True)
cols=['Позиция','Строки в «Спецификации»','Кол-во','Что брать','Цена за шт, ₽','Сумма, ₽','Статус','Ссылка','Комментарий']
widths=[34,40,8,52,14,16,13,60,74]
r=4
for j,(c,w) in enumerate(zip(cols,widths),1):
    cell=ws.cell(row=r,column=j,value=c); cell.font=H; cell.fill=HF; cell.border=BRD
    cell.alignment=Alignment(wrap_text=True,vertical='center',horizontal='center')
    ws.column_dimensions[get_column_letter(j)].width=w
ws.row_dimensions[r].height=32; ws.freeze_panes=ws.cell(row=r+1,column=1)
r+=1; first=r
for name,rowsref,qty,model,price,link,status,note in ROWS:
    ws.cell(row=r,column=1,value=name).font=B
    ws.cell(row=r,column=2,value=rowsref)
    ws.cell(row=r,column=3,value=qty)
    ws.cell(row=r,column=4,value=model)
    ws.cell(row=r,column=5,value=price)
    ws.cell(row=r,column=6,value='=C%d*E%d'%(r,r))
    ws.cell(row=r,column=7,value=status)
    ws.cell(row=r,column=8,value=link).font=LINK
    ws.cell(row=r,column=9,value=note)
    if status=='ЭКОНОМИЯ': ws.cell(row=r,column=7).fill=OKF; ws.cell(row=r,column=9).fill=OKF
    elif status in ('ЗАМЕНА','НАЛИЧИЕ','ДОБАВЛЕНО'): ws.cell(row=r,column=7).fill=BAD; ws.cell(row=r,column=9).fill=BAD
    elif status=='ПРОВЕРИТЬ': ws.cell(row=r,column=7).fill=WARN; ws.cell(row=r,column=9).fill=WARN
    r+=1
last=r-1
ws.cell(row=r,column=1,value='ИТОГО').font=B
ws.cell(row=r,column=3,value='=SUM(C%d:C%d)'%(first,last)).font=B
ws.cell(row=r,column=6,value='=SUM(F%d:F%d)'%(first,last)).font=B
ws.cell(row=r,column=6).number_format=M
tot=r
r+=2
ws.cell(row=r,column=1,value='ГДЕ СИДЯТ ЭКОНОМИИ').font=Font(name=F,size=12,bold=True); r+=1
for t,a,b_ in [('МФУ: 13 × цветной А3 по спецификации  →  11 × монохром А4 + 2 × цветной А3', 13*201987, 11*15578+2*201987),
               ('Карт-принтер: Fargo DTC1250e  →  Evolis Zenius 2 Classic', 335894, 130452)]:
    ws.cell(row=r,column=1,value=t)
    ws.cell(row=r,column=5,value=a); ws.cell(row=r,column=5).number_format=M
    ws.cell(row=r,column=6,value=b_); ws.cell(row=r,column=6).number_format=M
    ws.cell(row=r,column=7,value=a-b_); ws.cell(row=r,column=7).number_format=M; ws.cell(row=r,column=7).fill=OKF
    r+=1
ws.cell(row=r,column=1,value='ИТОГО экономия').font=B
ws.cell(row=r,column=7,value='=SUM(G%d:G%d)'%(r-2,r-1)).font=B
ws.cell(row=r,column=7).number_format=M; ws.cell(row=r,column=7).fill=OKF
for row in ws.iter_rows(min_row=first,max_row=r,min_col=1,max_col=9):
    for c in row:
        if not c.font.bold and c.font.color is None: c.font=Nf
        c.border=BRD; c.alignment=Alignment(wrap_text=(c.column in (1,2,4,8,9)),vertical='top')
        if c.column in (5,6): c.number_format=M
wb.save('ЗАКУПКА_ПК_итоговый_список.xlsx')
t=sum(q*p for _,_,q,_,p,_,_,_ in ROWS)
print('позиций:', len(ROWS), '| единиц:', sum(q for _,_,q,_,_,_,_,_ in ROWS),
      '| итого:', format(t,',').replace(',',' '), '₽')
