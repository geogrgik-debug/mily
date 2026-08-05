import openpyxl, re, json
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from collections import defaultdict

SRC='SPEC_IOS_TH_ITOGOVIY_FILE2_ЭДЕМ-ИСПРАВЛЕНО.xlsx'
src=openpyxl.load_workbook(SRC, data_only=True, read_only=True)
sp=[[c.value for c in r] for r in src['Спецификация'].iter_rows()]
sv=[[c.value for c in r] for r in src['Сверка ТХ.2'].iter_rows()]
eden=json.load(open('eden.json'))
pat=re.compile(r'эдем',re.I)

F='Arial'
H=Font(name=F,size=10,bold=True,color='FFFFFF')
HF=PatternFill('solid',fgColor='2F5597')
B=Font(name=F,size=10,bold=True)
N=Font(name=F,size=10)
RED=Font(name=F,size=10,color='C00000')
YEL=PatternFill('solid',fgColor='FFFF00')
WARN=PatternFill('solid',fgColor='FFE699')
thin=Side(style='thin',color='BFBFBF')
BRD=Border(left=thin,right=thin,top=thin,bottom=thin)
MONEY='#,##0.00;[Red](#,##0.00);-'

wb=openpyxl.Workbook()

def head(ws,cols,widths,row=1):
    for j,(c,w) in enumerate(zip(cols,widths),1):
        cell=ws.cell(row=row,column=j,value=c); cell.font=H; cell.fill=HF; cell.border=BRD
        cell.alignment=Alignment(wrap_text=True,vertical='center',horizontal='center')
        ws.column_dimensions[get_column_letter(j)].width=w
    ws.row_dimensions[row].height=30
    ws.freeze_panes=ws.cell(row=row+1,column=1)

def art_clean(a): return re.sub(r'^\+ ЭВ\*2 ','',str(a or ''))

# ---------------- 1. СВОД ----------------
ws=wb.active; ws.title='Свод'
ws['A1']='ЭДЕМ-МЕБЕЛЬ — СВОД ПО СПЕЦИФИКАЦИИ (лист «Спецификация», раздел ТХ.2)'
ws['A1'].font=Font(name=F,size=13,bold=True)
ws['A2']='Источник: SPEC_IOS_TH_ITOGOVIY_FILE2.xlsx, лист «Спецификация», строки 2519–3863 (раздел ТХ.2. ОБОРУДОВАНИЕ, МЕБЕЛЬ И ИНВЕНТАРЬ ПО ПОМЕЩЕНИЯМ, 03-01/07.21П-ТХ.2.СО)'
ws['A2'].font=Font(name=F,size=9,italic=True)
ws['A3']='Файл ИСПРАВЛЕННЫЙ: фильтр по «Эдем» в колонке C «Тип, марка» отдаёт всю мебель — 68 строк, 96 шт. Количества с ВОР сходятся по всем 7 позициям.'
ws['A3'].font=Font(name=F,size=9,italic=True)

r=5
ws.cell(row=r,column=1,value='А. ПОЗИЦИИ СЕРИИ «ЭДЕМ-1» (мебель)').font=B
r+=1
cols=['Поз. по СО','Наименование','Артикул','Ед.','Кол-во, шт','Цена, руб. с НДС','Сумма, руб. с НДС','Строк в спец.','Строки']
head(ws,cols,[11,58,42,7,11,16,18,12,46],row=r)
hdr_row=r; r+=1
agg=defaultdict(lambda:[0,None,set()])
for o in eden:
    if not (o['art'] and pat.search(str(o['art']))): continue
    k=(str(o['poz']),str(o['name']),art_clean(o['art']))
    agg[k][0]+=o['qty'] or 0; agg[k][1]=o['price']; agg[k][2].add(o['row'])
start=r
for k,v in sorted(agg.items(), key=lambda x:-x[1][0]):
    ws.cell(row=r,column=1,value=k[0])
    ws.cell(row=r,column=2,value=k[1])
    ws.cell(row=r,column=3,value=k[2])
    ws.cell(row=r,column=4,value='шт.')
    ws.cell(row=r,column=5,value=v[0])
    c=ws.cell(row=r,column=6,value=v[1] if v[1] else None)
    if not v[1]: c.value='ЦЕНЫ НЕТ'; c.font=RED; c.fill=YEL
    ws.cell(row=r,column=7,value='=IF(ISNUMBER(F%d),E%d*F%d,0)'%(r,r,r))
    ws.cell(row=r,column=8,value=len(v[2]))
    ws.cell(row=r,column=9,value=', '.join(str(x) for x in sorted(v[2])))
    r+=1
end=r-1
ws.cell(row=r,column=2,value='ИТОГО серия «Эдем-1»').font=B
ws.cell(row=r,column=5,value='=SUM(E%d:E%d)'%(start,end)).font=B
ws.cell(row=r,column=7,value='=SUM(G%d:G%d)'%(start,end)).font=B
totA=r
r+=2

ws.cell(row=r,column=1,value='Б. ПОЗИЦИИ, ГДЕ ПОСТАВЩИК УКАЗАН КАК ООО «ЭДЕМ-МЕБЕЛЬ» (колонка E)').font=B
r+=1
head(ws,cols,[11,58,42,7,11,16,18,12,46],row=r); r+=1
agg2=defaultdict(lambda:[0,None,set()])
for o in eden:
    if not (o['sup'] and pat.search(str(o['sup']))): continue
    k=(str(o['poz']),str(o['name']),art_clean(o['art']))
    agg2[k][0]+=o['qty'] or 0; agg2[k][1]=o['price']; agg2[k][2].add(o['row'])
start2=r
for k,v in sorted(agg2.items(), key=lambda x:-x[1][0]):
    ws.cell(row=r,column=1,value=k[0]); ws.cell(row=r,column=2,value=k[1]); ws.cell(row=r,column=3,value=k[2])
    ws.cell(row=r,column=4,value='шт.'); ws.cell(row=r,column=5,value=v[0])
    c=ws.cell(row=r,column=6,value=v[1] if v[1] else None)
    if not v[1]: c.value='ЦЕНЫ НЕТ'; c.font=RED; c.fill=YEL
    ws.cell(row=r,column=7,value='=IF(ISNUMBER(F%d),E%d*F%d,0)'%(r,r,r))
    ws.cell(row=r,column=8,value=len(v[2]))
    ws.cell(row=r,column=9,value=', '.join(str(x) for x in sorted(v[2])))
    r+=1
end2=r-1
ws.cell(row=r,column=2,value='ИТОГО по поставщику ООО «Эдем-Мебель»').font=B
ws.cell(row=r,column=5,value='=SUM(E%d:E%d)'%(start2,end2)).font=B
ws.cell(row=r,column=7,value='=SUM(G%d:G%d)'%(start2,end2)).font=B
totB=r
r+=2
ws.cell(row=r,column=1,value='Примечание: блоки А и Б пересекаются по 11 строкам (позиции серии «Эдем-1», у которых поставщиком указано ООО «Эдем-Мебель»), поэтому складывать итоги А и Б нельзя.').font=Font(name=F,size=9,italic=True)
r+=1
ws.cell(row=r,column=1,value='Всего уникальных строк спецификации с упоминанием «Эдем»: 69 (лист «Все строки Эдем»).').font=Font(name=F,size=9,italic=True)

for row in ws.iter_rows(min_row=hdr_row+1,max_row=totB,min_col=1,max_col=9):
    for c in row:
        if not c.font.bold and c.font.color is None: c.font=N
        c.border=BRD
        if c.column in (6,7) and isinstance(c.value,(int,float)) or (c.column==7 and isinstance(c.value,str) and c.value.startswith('=')):
            c.number_format=MONEY
        if c.column==2 or c.column==3 or c.column==9: c.alignment=Alignment(wrap_text=True,vertical='top')

# ---------------- 2. ВСЕ СТРОКИ ----------------
ws2=wb.create_sheet('Все строки Эдем')
cols2=['Строка в «Спецификации»','Раздел','Помещение','Поз. по СО','Наименование и тех. характеристика','Тип, марка, артикул','Поставщик','Ед. изм.','Кол-во','Цена, руб. с НДС','Сумма, руб. с НДС','Примечание','Флаг']
head(ws2,cols2,[12,34,40,10,58,42,22,8,9,15,17,44,16])
r=2
for o in sorted(eden,key=lambda x:x['row']):
    ws2.cell(row=r,column=1,value=o['row'])
    ws2.cell(row=r,column=2,value=o['section'])
    ws2.cell(row=r,column=3,value=o['room'])
    ws2.cell(row=r,column=4,value=o['poz'])
    ws2.cell(row=r,column=5,value=o['name'])
    ws2.cell(row=r,column=6,value=o['art'])
    ws2.cell(row=r,column=7,value=o['sup'])
    ws2.cell(row=r,column=8,value=o['unit'])
    ws2.cell(row=r,column=9,value=o['qty'])
    ws2.cell(row=r,column=10,value=o['price'])
    ws2.cell(row=r,column=11,value='=IF(ISNUMBER(J%d),I%d*J%d,0)'%(r,r,r))
    ws2.cell(row=r,column=12,value=o['note'])
    f=ws2.cell(row=r,column=13,value='ЦЕНЫ НЕТ' if not o['price'] else 'проценено')
    if not o['price']:
        f.font=RED; f.fill=YEL
        for j in range(1,13): ws2.cell(row=r,column=j).fill=WARN
    r+=1
last2=r-1
ws2.cell(row=r,column=5,value='ИТОГО').font=B
ws2.cell(row=r,column=9,value='=SUM(I2:I%d)'%last2).font=B
ws2.cell(row=r,column=11,value='=SUM(K2:K%d)'%last2).font=B
for row in ws2.iter_rows(min_row=2,max_row=r,min_col=1,max_col=13):
    for c in row:
        if not c.font.bold and c.font.color is None: c.font=N
        c.border=BRD
        c.alignment=Alignment(wrap_text=True,vertical='top')
        if c.column in (10,11): c.number_format=MONEY

# ---------------- 3. ПО ПОМЕЩЕНИЯМ ----------------
ws3=wb.create_sheet('По помещениям')
head(ws3,['Помещение','Раздел','Поз.','Наименование','Артикул','Кол-во, шт','Цена','Сумма'],[42,32,10,56,42,11,14,16])
r=2
byroom=defaultdict(list)
for o in eden: byroom[(o['room'],o['section'])].append(o)
for (room,sec),g in sorted(byroom.items(), key=lambda x: min(o['row'] for o in x[1])):
    c=ws3.cell(row=r,column=1,value=room); c.font=B; c.fill=PatternFill('solid',fgColor='DDEBF7')
    ws3.cell(row=r,column=2,value=sec).font=Font(name=F,size=9,italic=True)
    for j in range(1,9): ws3.cell(row=r,column=j).border=BRD
    r+=1
    for o in sorted(g,key=lambda x:x['row']):
        ws3.cell(row=r,column=3,value=o['poz'])
        ws3.cell(row=r,column=4,value=o['name'])
        ws3.cell(row=r,column=5,value=o['art'])
        ws3.cell(row=r,column=6,value=o['qty'])
        pc=ws3.cell(row=r,column=7,value=o['price'] if o['price'] else None)
        if not o['price']: pc.value='ЦЕНЫ НЕТ'; pc.font=RED; pc.fill=YEL
        ws3.cell(row=r,column=8,value='=IF(ISNUMBER(G%d),F%d*G%d,0)'%(r,r,r))
        r+=1
ws3.cell(row=r,column=4,value='ИТОГО').font=B
ws3.cell(row=r,column=6,value='=SUM(F2:F%d)'%(r-1)).font=B
ws3.cell(row=r,column=8,value='=SUM(H2:H%d)'%(r-1)).font=B
for row in ws3.iter_rows(min_row=2,max_row=r,min_col=1,max_col=8):
    for c in row:
        if not c.font.bold and c.font.color is None: c.font=N
        c.border=BRD; c.alignment=Alignment(wrap_text=True,vertical='top')
        if c.column in (7,8): c.number_format=MONEY

# ---------------- 4. СВЕРКА С ВОР ----------------
ws4=wb.create_sheet('Сверка с ВОР')
ws4['A1']='Позиции мебели «Эдем-1» на листе «Сверка ТХ.2» (сверка СО ↔ ВОР 02-01-11)'
ws4['A1'].font=Font(name=F,size=12,bold=True)
head(ws4,['Наименование по ТХ.2.СО','Ед. изм.','Кол-во по СО','Кол-во по ВОР','Разница (ВОР−СО)','Строк в ВОРе','Статус'],[62,10,13,14,16,13,60],row=3)
keys=['Стол рабочий прямой','Тумба для оргтехники','Шкаф двухстворчатый','Тумба выкатная','Стол для переговоров','Стул для посетителей','Кресло компьютерное']
r=4
for v in sv:
    nm=str(v[0]) if v[0] else ''
    if any(k.lower() in nm.lower() for k in keys):
        for j in range(7): ws4.cell(row=r,column=j+1,value=v[j])
        if v[4] not in (None,0):
            for j in range(1,8): ws4.cell(row=r,column=j).fill=WARN
        r+=1
for row in ws4.iter_rows(min_row=4,max_row=r-1,min_col=1,max_col=7):
    for c in row:
        c.font=N; c.border=BRD; c.alignment=Alignment(wrap_text=True,vertical='top')

# ---------------- 5. ЧТО ПРОВЕРИТЬ ----------------
ws5=wb.create_sheet('Что исправлено')
ws5['A1']='ЭДЕМ-МЕБЕЛЬ — ЧТО ИСПРАВЛЕНО И ЧТО ОСТАЛОСЬ'
ws5['A1'].font=Font(name=F,size=13,bold=True)
head(ws5,['№','Что сделано / что осталось','Где смотреть','Результат'],[6,92,44,40],row=3)
items=[
 ('1','ИСПРАВЛЕНО. Файл был сохранён с включённым фильтром по колонке B «Наименование» (3 значения) — скрыто 4311 строк из 4705. Именно поэтому поиск по «Эдем» отдавал не всё. Фильтр снят, все строки показаны.','Спецификация, автофильтр A1:K4339','Фильтр по «Эдем» теперь видит весь лист'),
 ('2','ИСПРАВЛЕНО. Пять строк стола 1200х700х750 (стр. 2514, 2608, 3773, 3851, 3859) шли с пустой колонкой C и с лишним пробелом в наименовании — фильтр по «Эдем» их не находил. Наименование приведено к единому виду, проставлен арт.Э-21.0.','Спецификация, колонка B и C','В фильтр вернулись 9 шт'),
 ('3','ИСПРАВЛЕНО. Стр.3252: из артикула убран мусорный префикс «+ ЭВ*2», приклеившийся от соседней строки 3251.','Спецификация, стр.3252, колонка C','Артикул стал единообразным'),
 ('4','ИСПРАВЛЕНО. Поставщик приведён к единому написанию «ООО «Эдем-Мебель»» в 17 строках — было «ООО «Эдем- мебель»» ×2 и «ООО «Эдем- Мебель»» ×15, в обоих лишний пробел после дефиса.','Спецификация, колонка E','Группировка по поставщику работает'),
 ('5','ИСПРАВЛЕНО. Разрыв внутри слова: «Торгов ая сеть Россия» → «Торговая сеть Россия», 13 строк.','Спецификация, колонка E','Фильтр по поставщику не двоится'),
 ('6','СВЕРКА СОШЛАСЬ. После объединения двух написаний стола 1200 количество по СО = 10 шт = количество по ВОР = 10 шт. Расхождение «ВОР больше СО на 1» было артефактом орфографии. По всем 7 позициям «Эдем-1»: 28/28, 23/23, 16/16, 12/12, 10/10, 6/6, 1/1 — расхождений нет.','Лист «Сверка с ВОР»','Объёмы подтверждены, править не нужно'),
 ('7','ОСТАЛОСЬ. Мебель «Эдем-1» по-прежнему без цены: 68 строк, 96 шт, колонка H пустая, сумма 0 руб. Проценены только не-эдемовские позиции у этого поставщика — стул Gigant (1950 руб.) и кресло Prestige (12 000 руб.), итого 29 550 руб.','Спецификация, колонка H','Стоимость раздела ТХ.2 занижена на всю мебель'),
 ('8','ОСТАЛОСЬ, НУЖНО РЕШЕНИЕ. В 52 строках из 68 поставщиком указано обезличенное «Торговая сеть Россия», а не «ООО «Эдем-Мебель»» — при том, что позиции те же. Это не орфография, а вопрос к договору: менять не стал.','Спецификация, колонка E','Непонятно, кто реально поставляет'),
 ('9','ПОД ВОПРОСОМ. «Стол рабочий прямой; 1600х700х750(h) мм» (поз. м221, 5 строк, 7 шт) — та же офисная линейка, но артикул не проставлен нигде в файле. Признаков «Эдема» нет, поэтому не трогал. Если это тоже Эдем — нужен артикул.','Спецификация, стр. 2535, 2556, 2568, 2620 и др.','7 шт вне фильтра по «Эдем»'),
 ('10','ПОД ВОПРОСОМ, вне Эдема. Поставщик «Электротехника и Авто- матика» (5 строк) — разрыв внутри слова, но канонического написания в файле нет, поэтому не исправлял.','Спецификация, колонка E','Мелкий мусор в справочнике поставщиков'),
]
r=4
r=4
for it in items:
    for j,val in enumerate(it,1):
        c=ws5.cell(row=r,column=j,value=val); c.font=N; c.border=BRD; c.alignment=Alignment(wrap_text=True,vertical='top')
    if it[0] in ('7','8','9','10'):
        for j in range(1,5): ws5.cell(row=r,column=j).fill=WARN
    ws5.row_dimensions[r].height=46
    r+=1

wb.save('EDEM_MEBEL_vygruzka.xlsx')
print('saved')
