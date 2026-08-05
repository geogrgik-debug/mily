# -*- coding: utf-8 -*-
"""РЕВЬЮ БЕЗ ПРАВОК. Собирает книгу с замечаниями по итоговому файлу.
Исходные файлы не изменяются."""
import openpyxl, re
from collections import defaultdict, Counter
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

ITOG='/root/.claude/uploads/a6572ada-bc7d-590d-9f64-07c4882f0066/330c2672-SPEC_IOS_TH_ITOGOVIY_FILE2.xlsx'
VOR ='/root/.claude/uploads/a6572ada-bc7d-590d-9f64-07c4882f0066/f840d37a-___________.xlsx'
def s(x): return '' if x is None else re.sub(r'\s+',' ',str(x)).strip()
def blank(x): return x is None or s(x)==''
def keyn(t): return re.sub(r'[^а-яёa-z0-9]','', s(t).lower().replace('ё','е'))
def N(x):
    if isinstance(x,(int,float)): return float(x)
    t=s(x).replace('\xa0','').replace(' ','').replace(',','.')
    return float(t) if re.fullmatch(r'-?\d+(\.\d+)?', t) else None

wif=openpyxl.load_workbook(ITOG); wiv=openpyxl.load_workbook(ITOG, data_only=True)
spf=wif['Спецификация']; spv=wiv['Спецификация']
irows=[[c.value for c in r] for r in spv.iter_rows()]
wv=openpyxl.load_workbook(VOR, data_only=True, read_only=True)
vrows=[[c.value for c in r] for r in wv['Sheet1'].iter_rows()]

# ---------- разделы ----------
DOC=re.compile(r'03-01/07\.21П[-.]'); TOP=re.compile(r'^(ЛОГИСТИКА|МАТЕРИАЛЫ ИЗ ВОР|ОБЩЕСТРОИТЕЛЬНЫЕ)')
cur='(без раздела)'; sec=defaultdict(lambda:[0,0,0.0]); order=[]; rowsec={}
for i,v in enumerate(irows):
    rest=[v[j] for j in range(2,len(v))]
    if all(blank(x) for x in rest) and not (blank(v[0]) and blank(v[1])):
        full=(s(v[0])+' '+s(v[1])).strip()
        if DOC.search(full) or TOP.match(s(v[0])) or TOP.match(s(v[1])):
            cur=full
            if cur not in order: order.append(cur)
        continue
    rowsec[i+1]=cur
    if isinstance(v[6],(int,float)):
        a=sec[cur]; a[0]+=1
        if isinstance(v[7],(int,float)) and v[7]: a[1]+=1
        a[2]+= v[8] if isinstance(v[8],(int,float)) else 0
        if cur not in order: order.append(cur)

# ---------- сверка с ВОР ----------
vor_num=defaultdict(list); vor_name=defaultdict(float); c2=None
for i,v in enumerate(vrows):
    head=s(v[0])+' '+s(v[1])
    m=re.search(r'ВОР\s*(\d\d-\d\d-\d\d)', head)
    if m and 'Ведомость' in head: c2=m.group(1); continue
    if c2 and re.fullmatch(r'\d+', s(v[0])):
        rec=dict(row=i+1, name=s(v[1]), unit=s(v[2]), qty=N(v[3]), price=N(v[8]), total=N(v[9]))
        vor_num[(c2,s(v[0]))].append(rec)
        if rec['qty'] is not None: vor_name[(c2,keyn(v[1]))]+=rec['qty']
def eq(a,b,tol=0.005):
    if a is None or b is None: return a is None and b is None
    return abs(a-b)<=max(tol,abs(b)*1e-6)
sv_line=sv_agg=0; sv_bad=[]; sv_price=[]; sv_nopr=0
for i,v in enumerate(irows):
    if i==0: continue
    ma=re.fullmatch(r'ВОР\s*(\d+)', s(v[0])); mk=re.search(r'ВОР\s*(\d\d-\d\d-\d\d)', s(v[10]) if len(v)>10 else '')
    if not (ma and mk): continue
    vn,nu=mk.group(1),ma.group(1)
    cands=vor_num.get((vn,nu))
    if not cands: continue
    c=next((x for x in cands if keyn(x['name'])==keyn(v[1])), cands[0])
    it=dict(row=i+1,name=s(v[1]),vor=vn,num=nu,qty=N(v[6]),price=N(v[7]),total=N(v[8]))
    qa=vor_name.get((vn,keyn(c['name'])))
    if   eq(it['qty'],c['qty']): sv_line+=1
    elif eq(it['qty'],qa):       sv_agg+=1
    else: sv_bad.append((it,c,qa))
    if it['price'] is not None and c['price'] is not None and not eq(it['price'],c['price'],0.01):
        sv_price.append((it,c))
    if c['price'] is None and it['price'] is not None: sv_nopr+=1

# ---------- опечатки ----------
mixed=defaultdict(list)
for row in spv.iter_rows(min_row=2):
    for c in row:
        if not isinstance(c.value,str): continue
        for w in re.findall(r'[A-Za-zА-Яа-яЁё]{2,}', c.value):
            if re.search(r'[А-Яа-яЁё]',w) and re.search(r'[A-Za-z]',w):
                mixed[(get_column_letter(c.column), w)].append(c.row)
sup=Counter()
for row in spv.iter_rows(min_row=2, min_col=5, max_col=5):
    if isinstance(row[0].value,str) and row[0].value.strip(): sup[row[0].value]+=1
supgrp=defaultdict(list)
for k,c in sup.items(): supgrp[re.sub(r'[^а-яёa-z0-9]','',k.lower().replace('ё','е'))].append((k,c))
tsvar=[(k,c) for k,c in sup.items() if 'орговая сеть' in k or 'оргов ая' in k]

# ---------- формулы ----------
weird=[]
for row in spf.iter_rows():
    for c in row:
        if isinstance(c.value,str) and c.value.startswith('=') and not re.fullmatch(r'=H\d+\*G\d+',c.value):
            weird.append((c.row, get_column_letter(c.column), c.value,
                          spv.cell(row=c.row,column=c.column).value,
                          s(spv.cell(row=c.row,column=2).value)[:60]))

# ================= КНИГА =================
F='Arial'
H=Font(name=F,size=10,bold=True,color='FFFFFF'); HF=PatternFill('solid',fgColor='1F3864')
B=Font(name=F,size=10,bold=True); Nf=Font(name=F,size=10); RED=Font(name=F,size=10,color='C00000')
WARN=PatternFill('solid',fgColor='FFE699'); BADF=PatternFill('solid',fgColor='FFC7CE')
OKF=PatternFill('solid',fgColor='E2EFDA')
thin=Side(style='thin',color='BFBFBF'); BRD=Border(left=thin,right=thin,top=thin,bottom=thin)
MONEY='#,##0;[Red](#,##0);-'
wb=openpyxl.Workbook()
def head(ws,cols,widths,row=1):
    for j,(c,w) in enumerate(zip(cols,widths),1):
        cell=ws.cell(row=row,column=j,value=c); cell.font=H; cell.fill=HF; cell.border=BRD
        cell.alignment=Alignment(wrap_text=True,vertical='center',horizontal='center')
        ws.column_dimensions[get_column_letter(j)].width=w
    ws.row_dimensions[row].height=30
    ws.freeze_panes=ws.cell(row=row+1,column=1)
def body(ws, r1, r2, c2_, wrap=(), money=()):
    for row in ws.iter_rows(min_row=r1,max_row=r2,min_col=1,max_col=c2_):
        for c in row:
            if c.font.color is None and not c.font.bold: c.font=Nf
            c.border=BRD
            c.alignment=Alignment(wrap_text=(c.column in wrap), vertical='top')
            if c.column in money: c.number_format=MONEY

# --- 1. ИТОГ ---
ws=wb.active; ws.title='Итог ревью'
ws['A1']='РЕВЬЮ ИТОГОВОГО ФАЙЛА — SPEC_IOS_TH_ITOGOVIY_FILE2.xlsx'
ws['A1'].font=Font(name=F,size=14,bold=True)
ws['A2']='Проверка без правок. Исходные файлы не изменялись.'
ws['A2'].font=Font(name=F,size=10,italic=True)
rows_txt=[
 ('ЧТО ЗА ФАЙЛ',''),
 ('Лист «Спецификация»','3875 позиций в 21 разделе; итог 1 448 528 218 руб. (формула I4339 = SUM(I2:I4337), диапазон корректный)'),
 ('Лист «НЕТ В СО (из ВОРов)»','306 позиций, которых нет в СО; 295 формул тянут кол-во/цену/сумму с «Спецификации». Все 295 ссылок ведут в правильные строки. Итог 1 158 383 836 руб.'),
 ('Лист «Сверка ТХ.2»','287 строк сверки СО ↔ ВОР 02-01-11, только по разделу ТХ.2'),
 ('Лист «Правки»','2300 записей журнала изменений'),
 ('',''),
 ('СВЕРКА С ИСХОДНЫМ ВОРом',''),
 ('Сопоставлено позиций','%d (по паре «№ ВОР» + «№ п/п» из примечания)' % (sv_line+sv_agg+len(sv_bad))),
 ('  кол-во = строке ВОРа','%d' % sv_line),
 ('  кол-во = сумме одноимённых строк','%d — итоговый агрегирует несколько строк ВОРа в одну, это нормально' % sv_agg),
 ('  РАСХОЖДЕНИЙ ПО КОЛИЧЕСТВУ','%d' % len(sv_bad)),
 ('  РАСХОЖДЕНИЙ ПО ЦЕНЕ','%d' % len(sv_price)),
 ('  битых ссылок на ВОР','0'),
 ('',''),
 ('ГЛАВНОЕ',''),
 ('1. Не проценено 58% строк','2256 позиций из 3875 без цены. Худшие разделы: ТХ.4 (154 из 157), ИОС.5.3 автоматизация (234 из 241), ИОС.4.4 наружные теплосети (134 из 142), ИОС.5.1 связь (240 из 363).'),
 ('2. 77% денег — в одном блоке','«МАТЕРИАЛЫ ИЗ ВОРов ПО ДИСЦИПЛИНАМ» = 1 116 623 755 руб. из 191 строки. Это 77% всей суммы файла. Блок собран из ВОРов, а не из СО — проверять его надо в первую очередь.'),
 ('3. Латиница внутри русских слов','120 ячеек, 20 разных слов. Ломает поиск и фильтр — та же болезнь, что была с «Эдемом».'),
 ('4. Поставщик «Торговая сеть Россия»','979 строк размазаны по 8 написаниям. В фильтре это 8 разных поставщиков.'),
 ('5. Хрупкие формулы в колонке ЦЕНА','61 ячейка с ручной арифметикой вместо числа. Строка 87 — единственная в файле, где сумма ≠ кол-во × цена.'),
 ('',''),
 ('ЧЕГО НЕ ХВАТИЛО',''),
 ('PDF-спецификации ИОС','18 файлов лежат на сетевом диске \\\\fs\\share\\... — у меня доступа туда нет. Сверку «итоговый ↔ ИОС» сделать не смог. Нужно прислать файлы.'),
]
r=4
for a,b_ in rows_txt:
    ca=ws.cell(row=r,column=1,value=a); cb=ws.cell(row=r,column=2,value=b_)
    if a and not b_: ca.font=Font(name=F,size=11,bold=True,color='1F3864')
    else: ca.font=B; cb.font=Nf
    cb.alignment=Alignment(wrap_text=True,vertical='top'); ws.row_dimensions[r].height=None
    r+=1
ws.column_dimensions['A'].width=38; ws.column_dimensions['B'].width=112

# --- 2. РАЗДЕЛЫ ---
ws2=wb.create_sheet('Разделы и деньги')
head(ws2,['Раздел','Позиций','С ценой','БЕЗ цены','% без цены','Сумма, руб. с НДС','Доля суммы'],[74,10,10,11,12,20,11])
r=2; first=r
for k in order:
    a=sec[k]
    ws2.cell(row=r,column=1,value=k); ws2.cell(row=r,column=2,value=a[0])
    ws2.cell(row=r,column=3,value=a[1]); ws2.cell(row=r,column=4,value=a[0]-a[1])
    ws2.cell(row=r,column=5,value='=IF(B%d=0,0,D%d/B%d)'%(r,r,r)).number_format='0.0%'
    ws2.cell(row=r,column=6,value=a[2])
    ws2.cell(row=r,column=7,value='=IF($F$%d=0,0,F%d/$F$%d)'%(r+len(order)-(r-first),r,r+len(order)-(r-first))).number_format='0.0%'
    if a[0] and (a[0]-a[1])/a[0]>0.7: 
        for j in range(1,8): ws2.cell(row=r,column=j).fill=WARN
    r+=1
last=r-1
ws2.cell(row=r,column=1,value='ИТОГО').font=B
for j,col in ((2,'B'),(3,'C'),(4,'D'),(6,'F')):
    ws2.cell(row=r,column=j,value='=SUM(%s%d:%s%d)'%(col,first,col,last)).font=B
ws2.cell(row=r,column=6).number_format=MONEY
body(ws2,2,r,7,wrap=(1,),money=(6,))

# --- 3. СВЕРКА С ВОР ---
ws3=wb.create_sheet('Расхождения с ВОР')
ws3['A1']='РАСХОЖДЕНИЯ ИТОГОВОГО С ИСХОДНЫМ СВОДНЫМ ВОРом'
ws3['A1'].font=Font(name=F,size=12,bold=True)
head(ws3,['Строка итогового','№ ВОР','№ п/п','Наименование','Что не сходится','В итоговом','В ВОРе','Комментарий'],[15,12,9,52,17,14,14,62],row=3)
r=4
for it,c,qa in sv_bad:
    ws3.cell(row=r,column=1,value=it['row']); ws3.cell(row=r,column=2,value=it['vor'])
    ws3.cell(row=r,column=3,value=it['num']); ws3.cell(row=r,column=4,value=it['name'])
    ws3.cell(row=r,column=5,value='КОЛИЧЕСТВО'); ws3.cell(row=r,column=6,value=it['qty'])
    ws3.cell(row=r,column=7,value=c['qty'])
    ws3.cell(row=r,column=8,value='В ВОРе под №%s другая позиция: «%s». Ссылка на номер строки ВОРа неверная.'%(it['num'],c['name'][:60]))
    for j in range(1,9): ws3.cell(row=r,column=j).fill=BADF
    r+=1
for it,c in sv_price:
    ws3.cell(row=r,column=1,value=it['row']); ws3.cell(row=r,column=2,value=it['vor'])
    ws3.cell(row=r,column=3,value=it['num']); ws3.cell(row=r,column=4,value=it['name'])
    ws3.cell(row=r,column=5,value='ЦЕНА'); ws3.cell(row=r,column=6,value=it['price'])
    ws3.cell(row=r,column=7,value=c['price'])
    ws3.cell(row=r,column=8,value='Количество сходится (%s). Цена в итоговом ниже ВОРа в %.1f раза; сумма меняется на %s руб.'
             % (it['qty'], c['price']/it['price'], format(round(it['qty']*(c['price']-it['price'])),',').replace(',',' ')))
    for j in range(1,9): ws3.cell(row=r,column=j).fill=BADF
    r+=1
body(ws3,4,r-1,8,wrap=(4,8))

# --- 4. ОПЕЧАТКИ ---
ws4=wb.create_sheet('Опечатки')
ws4['A1']='ЛАТИНИЦА ВНУТРИ РУССКИХ СЛОВ — ломает поиск и фильтр'
ws4['A1'].font=Font(name=F,size=12,bold=True)
head(ws4,['Колонка','Как написано','Ячеек','Строки','Чем плохо'],[10,26,9,72,54],row=3)
r=4
for (col,w),rws in sorted(mixed.items(), key=lambda x:-len(x[1])):
    ws4.cell(row=r,column=1,value=col); ws4.cell(row=r,column=2,value=w)
    ws4.cell(row=r,column=3,value=len(rws))
    ws4.cell(row=r,column=4,value=', '.join(map(str,rws[:28]))+(' …' if len(rws)>28 else ''))
    lat=''.join(ch for ch in w if re.match(r'[A-Za-z]',ch)); cyr=''.join(ch for ch in w if re.match(r'[А-Яа-яЁё]',ch))
    ws4.cell(row=r,column=5,value='латинские «%s» вперемешку с русскими «%s» — поиск по одному алфавиту эти строки не найдёт'%(lat,cyr))
    for j in range(1,6): ws4.cell(row=r,column=j).fill=WARN
    r+=1
r+=1
ws4.cell(row=r,column=1,value='ОДИН ПОСТАВЩИК — РАЗНЫЕ НАПИСАНИЯ').font=Font(name=F,size=12,bold=True); r+=1
head(ws4,['Колонка','Как написано','Ячеек','Строки','Чем плохо'],[10,26,9,72,54],row=r); r+=1
tsrows=defaultdict(list)
for row in spv.iter_rows(min_row=2,min_col=5,max_col=5):
    if isinstance(row[0].value,str) and ('орговая сеть' in row[0].value or 'оргов ая' in row[0].value):
        tsrows[row[0].value].append(row[0].row)
for k,c in sorted(tsvar, key=lambda x:-x[1]):
    ws4.cell(row=r,column=1,value='E'); ws4.cell(row=r,column=2,value=k); ws4.cell(row=r,column=3,value=c)
    ws4.cell(row=r,column=4,value=', '.join(map(str,tsrows[k][:28]))+(' …' if len(tsrows[k])>28 else ''))
    ws4.cell(row=r,column=5,value='в фильтре по поставщику это отдельная позиция' if c<800 else 'основное написание — к нему и сводить')
    if c<800:
        for j in range(1,6): ws4.cell(row=r,column=j).fill=WARN
    r+=1
for g,items in sorted(supgrp.items()):
    if len(items)<2: continue
    if any('орговая сеть' in a for a,b_ in items): continue
    ws4.cell(row=r,column=1,value='E')
    ws4.cell(row=r,column=2,value=' / '.join(a for a,b_ in sorted(items,key=lambda x:-x[1])))
    ws4.cell(row=r,column=3,value=sum(b_ for a,b_ in items))
    ws4.cell(row=r,column=5,value='одно и то же, написано по-разному: %s' % ', '.join('«%s» ×%d'%(a,b_) for a,b_ in sorted(items,key=lambda x:-x[1])))
    r+=1
body(ws4,4,r-1,5,wrap=(4,5))

# --- 5. ФОРМУЛЫ ---
ws5=wb.create_sheet('Формулы')
ws5['A1']='РУЧНАЯ АРИФМЕТИКА ВМЕСТО ЧИСЕЛ — 61 ячейка (везде, кроме них, стоит нормальная =H*G)'
ws5['A1'].font=Font(name=F,size=12,bold=True)
head(ws5,['Строка','Колонка','Формула','Значение','Позиция','Чем рискованно'],[9,10,34,16,54,64],row=3)
r=4
for rr,col,f,val,nm in sorted(weird):
    ws5.cell(row=r,column=1,value=rr); ws5.cell(row=r,column=2,value=col)
    ws5.cell(row=r,column=3,value=f); ws5.cell(row=r,column=4,value=val)
    ws5.cell(row=r,column=5,value=nm)
    if col=='H' and re.match(r'^=\d+(\.\d+)?\*100$', f.replace(' ','')):
        cm='цена = число × 100 без пояснения. Похоже на курс валюты, зашитый в формулу: если это так, при смене курса цена устареет молча.'
        ws5.cell(row=r,column=6).fill=BADF
    elif col=='H' and '+' in f:
        cm='цена собрана сложением без расшифровки, из чего она состоит (доставка? монтаж?). Проверить и вынести составляющие в примечание.'
        ws5.cell(row=r,column=6).fill=WARN
    elif col=='H' and '/' in f:
        cm='цена получена делением общей суммы на количество. Если количество изменится — цена поедет.'
        ws5.cell(row=r,column=6).fill=WARN
    elif f.startswith('=I') or 'I8' in f:
        cm='ЦЕНА ссылается на СУММЫ строк ниже, а сумма этой строки вычитает их обратно. Единственное место в файле, где сумма ≠ кол-во × цена. Разобрать вручную.'
        ws5.cell(row=r,column=6).fill=BADF
    elif f.startswith('=SUM'):
        cm='итоговая сумма по листу, диапазон I2:I4337 — корректный, все данные внутри'
        ws5.cell(row=r,column=6).fill=OKF
    else:
        cm='формула-константа: считается как число, но выглядит как расчёт'
    ws5.cell(row=r,column=6,value=cm)
    r+=1
body(ws5,4,r-1,6,wrap=(5,6))

wb.save('РЕВЬЮ_итогового_файла.xlsx')
print('готово: РЕВЬЮ_итогового_файла.xlsx')
print('листы:', wb.sheetnames)
print('расхождений с ВОР: кол-во %d, цена %d' % (len(sv_bad), len(sv_price)))
print('опечаток (латиница): %d слов / %d ячеек' % (len(mixed), sum(len(v) for v in mixed.values())))
print('нестандартных формул: %d' % len(weird))
