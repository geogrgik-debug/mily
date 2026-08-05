# -*- coding: utf-8 -*-
"""Дописывает в книгу ревью лист «Документы и пути». Исходные файлы не трогает."""
import openpyxl, re, json
from collections import Counter, defaultdict
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
SRC='/root/.claude/uploads/a6572ada-bc7d-590d-9f64-07c4882f0066/330c2672-SPEC_IOS_TH_ITOGOVIY_FILE2.xlsx'
def s(x): return '' if x is None else re.sub(r'\s+',' ',str(x)).strip()
wb0=openpyxl.load_workbook(SRC, data_only=True); ws0=wb0['Спецификация']
files=Counter(); rowsof=defaultdict(list)
for row in ws0.iter_rows(min_row=2):
    for c in row:
        t=s(c.value)
        if 'Файл:' not in t: continue
        for m in re.finditer(r'Файл:\s*([^,)]+?)(?:,\s*стр|\))', t):
            fn=m.group(1).strip(); files[fn]+=1; rowsof[fn].append(c.row)
def doc_of(fn):
    t=fn.upper(); t=re.split(r'21\s*П', t, maxsplit=1)[-1]
    t=t.replace(' ','').replace('_','').replace('—','-').replace('.PDF','')
    t=re.sub(r'СТР.*$','',t); t=t.strip('-!#. ').replace('.','')
    for pre,name in (('АР','АР.1'),('ПЗУ','ПЗУ'),('ПОС','ПОС'),('КР','КР'),('ТХ2','ТХ.2'),('ТХ3','ТХ.3'),
                     ('ИОС44','ИОС.4.4'),('ИОС43','ИОС.4.3'),('ИОС42','ИОС.4.2'),('ИОС32','ИОС.3.2'),
                     ('ИОС31','ИОС.3.1'),('ИОС51','ИОС.5.1'),('ИОС15','ИОС.1.5'),('ИОС14','ИОС.1.4'),
                     ('ИОС2','ИОС.2.1')):
        if t.startswith(pre): return name
    return {'ПБ2':'ПБ2','NB2':'ПБ2','AK1':'АК.1','HCOM':'HCOM'}.get(t, t or '(пусто)')
doc=defaultdict(lambda:[0,set(),set()])
for f,n in files.items():
    d=doc_of(f); doc[d][0]+=n; doc[d][1].add(f); doc[d][2] |= set(rowsof[f])
SENT={'ИОС.1.1','ИОС.1.2','ИОС.1.3','ИОС.1.4','ИОС.1.5','ИОС.2.1','ИОС.3.1','ИОС.3.2','ИОС.4.1',
      'ИОС.4.2','ИОС.4.3','ИОС.4.4','ИОС.5.1','ИОС.5.2','ИОС.5.3','КР','ПБ2','ТХ.4'}
NAMES={'АР.1':'Архитектурные решения','ПЗУ':'Планировка земельного участка','ПОС':'Проект организации строительства',
       'КР':'Конструктивные решения','ТХ.2':'Оборудование, мебель и инвентарь','ТХ.3':'Технология (часть 3)',
       'АК.1':'Архитектурные конструкции','HCOM':'не расшифровывается — код битый',
       'ИОС.2.1':'Водоснабжение','ИОС.3.1':'Канализация','ИОС.3.2':'Дренаж','ИОС.4.2':'Вентиляция и кондиционирование',
       'ИОС.4.3':'ИТП','ИОС.4.4':'Наружные сети теплоснабжения','ИОС.5.1':'Сети связи','ИОС.1.4':'Наружное электроосвещение',
       'ИОС.1.5':'Внешние сети электроснабжения','ПБ2':'Пожарная сигнализация'}

F='Arial'
H=Font(name=F,size=10,bold=True,color='FFFFFF'); HF=PatternFill('solid',fgColor='1F3864')
B=Font(name=F,size=10,bold=True); Nf=Font(name=F,size=10)
BADF=PatternFill('solid',fgColor='FFC7CE'); OKF=PatternFill('solid',fgColor='E2EFDA'); WARN=PatternFill('solid',fgColor='FFE699')
thin=Side(style='thin',color='BFBFBF'); BRD=Border(left=thin,right=thin,top=thin,bottom=thin)

wb=openpyxl.load_workbook('РЕВЬЮ_итогового_файла.xlsx')
if 'Документы и пути' in wb.sheetnames: del wb['Документы и пути']
ws=wb.create_sheet('Документы и пути', 1)
ws['A1']='НА КАКИЕ ИСХОДНЫЕ ДОКУМЕНТЫ ССЫЛАЕТСЯ ИТОГОВЫЙ И ЧТО ИЗ ЭТОГО ПРИСЛАНО'
ws['A1'].font=Font(name=F,size=13,bold=True)
ws['A2']='Проверено по колонке «Примечание» листа «Спецификация»: 306 позиций содержат ссылку «(Файл: …)». Пути \\\\fs\\share\\… технически недоступны — сетевых монтирований нет, хост fs не резолвится, smb-клиента в системе нет.'
ws['A2'].font=Font(name=F,size=9,italic=True); ws['A2'].alignment=Alignment(wrap_text=True)
cols=['Документ','Что это','Позиций ссылается','Написаний имени файла','Прислан?','Строки в «Спецификации»']
widths=[11,42,12,12,17,66]
r=4
for j,(c,w) in enumerate(zip(cols,widths),1):
    cell=ws.cell(row=r,column=j,value=c); cell.font=H; cell.fill=HF; cell.border=BRD
    cell.alignment=Alignment(wrap_text=True,vertical='center',horizontal='center')
    ws.column_dimensions[get_column_letter(j)].width=w
ws.row_dimensions[r].height=32; ws.freeze_panes=ws.cell(row=r+1,column=1)
r+=1; first=r
for d,(n,fs,rws) in sorted(doc.items(), key=lambda x:-x[1][0]):
    ok = d in SENT
    ws.cell(row=r,column=1,value=d); ws.cell(row=r,column=2,value=NAMES.get(d,''))
    ws.cell(row=r,column=3,value=n); ws.cell(row=r,column=4,value=len(fs))
    ws.cell(row=r,column=5,value='да' if ok else 'НЕТ — нужен')
    rl=sorted(rws)
    ws.cell(row=r,column=6,value=', '.join(map(str,rl[:26]))+(' …' if len(rl)>26 else ''))
    for j in range(1,7): ws.cell(row=r,column=j).fill = OKF if ok else BADF
    r+=1
last=r-1
ws.cell(row=r,column=2,value='ИТОГО позиций со ссылкой на файл').font=B
ws.cell(row=r,column=3,value='=SUM(C%d:C%d)'%(first,last)).font=B
tot=r
r+=2
ws.cell(row=r,column=1,value='ОДИН ДОКУМЕНТ — РАЗНЫЕ ИМЕНА ФАЙЛА В ПРИМЕЧАНИЯХ').font=Font(name=F,size=12,bold=True); r+=1
for j,(c,w) in enumerate(zip(['Документ','Как записано имя файла','Позиций'],[11,66,10]),1):
    cell=ws.cell(row=r,column=j,value=c); cell.font=H; cell.fill=HF; cell.border=BRD
r+=1
for d,(n,fs,rws) in sorted(doc.items(), key=lambda x:-len(x[1])):
    if len(fs)<2: continue
    for f in sorted(fs):
        ws.cell(row=r,column=1,value=d); ws.cell(row=r,column=2,value=f)
        ws.cell(row=r,column=3,value=files[f])
        if re.search(r'NB2|HCOM|р#|!', f, re.I): ws.cell(row=r,column=2).fill=BADF
        else: ws.cell(row=r,column=2).fill=WARN
        r+=1
for row in ws.iter_rows(min_row=first,max_row=r-1,min_col=1,max_col=6):
    for c in row:
        if not c.font.bold: c.font=Nf
        c.border=BRD; c.alignment=Alignment(wrap_text=(c.column in (2,6)),vertical='top')
wb.save('РЕВЬЮ_итогового_файла.xlsx')
print('лист «Документы и пути» добавлен; документов:', len(doc),
      '| не прислано:', sum(1 for d in doc if d not in SENT),
      '| позиций без исходника:', sum(n for d,(n,_,_) in doc.items() if d not in SENT))
