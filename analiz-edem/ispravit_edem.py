# -*- coding: utf-8 -*-
"""Правка книги SPEC_IOS_TH_ITOGOVIY_FILE2.xlsx на уровне XML.
Цель: фильтр по «Эдем» в колонке C «Тип, марка» должен отдавать ВСЮ мебель Эдема.
Правка идёт по XML, а не через openpyxl, чтобы сохранить 5143 формулы с кэшированными
значениями, автофильтр, стили и объединённые ячейки исходного файла.
"""
import re, shutil, os, zipfile

SRC='/root/.claude/uploads/a6572ada-bc7d-590d-9f64-07c4882f0066/330c2672-SPEC_IOS_TH_ITOGOVIY_FILE2.xlsx'
WORK='xl_work'
OUT='SPEC_IOS_TH_ITOGOVIY_FILE2_ЭДЕМ-ИСПРАВЛЕНО.xlsx'

if os.path.exists(WORK): shutil.rmtree(WORK)
os.makedirs(WORK)
with zipfile.ZipFile(SRC) as z:
    names=z.namelist()
    z.extractall(WORK)

P=lambda p: os.path.join(WORK,p)
ss=open(P('xl/sharedStrings.xml'),encoding='utf-8').read()
s1=open(P('xl/worksheets/sheet1.xml'),encoding='utf-8').read()
s3=open(P('xl/worksheets/sheet3.xml'),encoding='utf-8').read()
s4=open(P('xl/worksheets/sheet4.xml'),encoding='utf-8').read()

# ---- таблица общих строк -------------------------------------------------
sis=re.findall(r'<si>(.*?)</si>', ss, re.S)
texts=[''.join(re.findall(r'<t[^>]*>(.*?)</t>', x, re.S)) for x in sis]
def unesc(t): return t.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>')
def esc(t):  return t.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
lookup={}
for i,t in enumerate(texts):
    lookup.setdefault(unesc(t), i)
new_si=[]
def S(text):
    """индекс общей строки; при отсутствии — добавляем новую"""
    if text in lookup: return lookup[text]
    idx=len(texts)+len(new_si)
    new_si.append(text); lookup[text]=idx
    return idx

log=[]           # (строка_спец, колонка, было, стало, что сделано)
report=[]

# ---- A. снять сохранённый активный фильтр и показать все строки ----------
m=re.search(r'<autoFilter ref="A1:K4339">.*?</autoFilter>', s1, re.S)
assert m, 'не найден активный autoFilter'
filt_vals=re.findall(r'<filter val="([^"]*)"', m.group(0))
s1=s1[:m.start()]+'<autoFilter ref="A1:K4339"/>'+s1[m.end():]
hidden_before=len(re.findall(r'<row [^>]*hidden="1"', s1))
s1=re.sub(r'(<row [^>]*?) hidden="1"', r'\1', s1)
assert len(re.findall(r'<row [^>]*hidden="1"', s1))==0
report.append(('A','Снят сохранённый в файле активный фильтр по колонке B «Наименование» (%d значения) и показаны все строки: было скрыто %d из 4705.'%(len(filt_vals),hidden_before)))

# ---- вспомогательное: заменить ссылку на общую строку в конкретной ячейке -
def repoint(xml, cellref, old_idx, new_idx):
    pat=re.compile(r'(<c r="%s"[^>]*t="s"[^>]*>)<v>%d</v>(</c>)'%(cellref, old_idx))
    xml2,n=pat.subn(lambda mm: mm.group(1)+'<v>%d</v>'%new_idx+mm.group(2), xml)
    assert n==1, 'ячейка %s: ожидалась 1 замена, вышло %d'%(cellref,n)
    return xml2

def fill_empty(xml, cellref, new_idx):
    pat=re.compile(r'<c r="%s"( s="\d+")?/>'%cellref)
    xml2,n=pat.subn(lambda mm: '<c r="%s"%s t="s"><v>%d</v></c>'%(cellref, mm.group(1) or '', new_idx), xml)
    assert n==1, 'пустая ячейка %s: ожидалась 1 замена, вышло %d'%(cellref,n)
    return xml2

SI_NAME_SPACE = 4261   # «Стол рабочий прямой; 1200х700х750 (h) мм»  (лишний пробел)
SI_NAME_OK    = 4988   # «Стол рабочий прямой; 1200х700х750(h) мм»
SI_ART_21     = 4989   # «Серия «Эдем-1», арт.Э-21.0»
SI_ART_267    = 4269   # «Серия «Эдем-1», арт.Э-26.7»
SI_ART_JUNK   = 4931   # «+ ЭВ*2 Серия «Эдем-1», арт.Э-26.7»
SI_SUP_LOW    = 4685   # «ООО «Эдем- мебель»»
SI_SUP_UP     = 4930   # «ООО «Эдем- Мебель»»
SI_TS_BROKEN  = 4245   # «Торгов ая сеть Россия»
SI_TS_OK      = 4228   # «Торговая сеть Россия»

# ---- B+C. столы 1200: единое наименование + артикул Э-21.0 ---------------
desk_rows=sorted(int(r) for r in re.findall(r'<c r="B(\d+)"[^>]*t="s"[^>]*><v>%d</v></c>'%SI_NAME_SPACE, s1))
assert len(desk_rows)==5, desk_rows
for r in desk_rows:
    s1=repoint(s1,'B%d'%r, SI_NAME_SPACE, SI_NAME_OK)
    s1=fill_empty(s1,'C%d'%r, SI_ART_21)
    log.append((r,'B — Наименование','Стол рабочий прямой; 1200х700х750 (h) мм','Стол рабочий прямой; 1200х700х750(h) мм',
                'наименование приведено к единому написанию — убран лишний пробел перед «(h)»'))
    log.append((r,'C — Тип, марка','','Серия «Эдем-1», арт.Э-21.0',
                'проставлен артикул серии «Эдем-1»: позиция идентична стр.3276 (тот же стол 1200х700х750). '
                'После объединения двух написаний кол-во по СО 10 шт = кол-во по ВОР 10 шт'))
report.append(('B','Наименование «Стол рабочий прямой; 1200х700х750 (h) мм» приведено к единому написанию в %d строках: %s.'%(len(desk_rows), ', '.join(map(str,desk_rows)))))
report.append(('C','Тем же %d строкам проставлен артикул «Серия «Эдем-1», арт.Э-21.0» — раньше колонка C была пустой и фильтр по «Эдем» эти 9 шт не находил.'%len(desk_rows)))

# ---- D. мусорный префикс «+ ЭВ*2» ---------------------------------------
junk=sorted(int(r) for r in re.findall(r'<c r="C(\d+)"[^>]*t="s"[^>]*><v>%d</v></c>'%SI_ART_JUNK, s1))
assert junk==[3252], junk
s1=repoint(s1,'C3252', SI_ART_JUNK, SI_ART_267)
log.append((3252,'C — Тип, марка','+ ЭВ*2 Серия «Эдем-1», арт.Э-26.7','Серия «Эдем-1», арт.Э-26.7',
            'убран мусорный префикс «+ ЭВ*2», приклеившийся от соседней позиции (стр.3251)'))
report.append(('D','Стр.3252: из артикула убран мусорный префикс «+ ЭВ*2».'))

# ---- E. поставщик к единому виду ----------------------------------------
SI_SUP_OK=S('ООО «Эдем-Мебель»')
sup_rows=[]
for old,was in ((SI_SUP_LOW,'ООО «Эдем- мебель»'),(SI_SUP_UP,'ООО «Эдем- Мебель»')):
    rows=sorted(int(r) for r in re.findall(r'<c r="E(\d+)"[^>]*t="s"[^>]*><v>%d</v></c>'%old, s1))
    for r in rows:
        s1=repoint(s1,'E%d'%r, old, SI_SUP_OK)
        log.append((r,'E — Поставщик',was,'ООО «Эдем-Мебель»',
                    'наименование поставщика приведено к единому написанию (было два варианта регистра и лишний пробел после дефиса)'))
    sup_rows+=rows
assert len(sup_rows)==17, sup_rows
report.append(('E','Поставщик приведён к единому виду «ООО «Эдем-Мебель»» в %d строках (было «ООО «Эдем- мебель»» ×2 и «ООО «Эдем- Мебель»» ×15).'%len(sup_rows)))

# ---- F. разрыв в «Торговая сеть Россия» ---------------------------------
ts_rows=sorted(int(r) for r in re.findall(r'<c r="E(\d+)"[^>]*t="s"[^>]*><v>%d</v></c>'%SI_TS_BROKEN, s1))
assert len(ts_rows)==13, ts_rows
for r in ts_rows:
    s1=repoint(s1,'E%d'%r, SI_TS_BROKEN, SI_TS_OK)
    log.append((r,'E — Поставщик','Торгов ая сеть Россия','Торговая сеть Россия','исправлен разрыв внутри слова «Торговая»'))
report.append(('F','Исправлен разрыв в слове «Торгов ая сеть Россия» → «Торговая сеть Россия» в %d строках.'%len(ts_rows)))

# ---- G. лист «Сверка ТХ.2»: объединить два написания стола 1200 ----------
SI_SV_OK  = S('сходится (объединены два написания одного наименования)')
SI_SV_NAME= S('(объединено) Стол рабочий прямой; 1200х700х750(h) мм')
SI_SV_ST  = S('объединено со строкой выше — два написания одного наименования')
row26=re.search(r'<row r="26"[^>]*>.*?</row>', s3, re.S).group(0)
new26=row26
new26=re.sub(r'(<c r="A26"[^>]*t="s"[^>]*>)<v>\d+</v>', r'\g<1><v>%d</v>'%SI_NAME_OK, new26)
new26=re.sub(r'(<c r="C26"[^>]*>)<v>\d+</v>', r'\g<1><v>10</v>', new26)
new26=re.sub(r'(<c r="E26"[^>]*>)<v>\d+</v>', r'\g<1><v>0</v>', new26)
new26=re.sub(r'(<c r="G26"[^>]*t="s"[^>]*>)<v>\d+</v>', r'\g<1><v>%d</v>'%SI_SV_OK, new26)
assert new26!=row26
s3=s3.replace(row26,new26)
row63=re.search(r'<row r="63"[^>]*>.*?</row>', s3, re.S).group(0)
new63=row63
new63=re.sub(r'(<c r="A63"[^>]*t="s"[^>]*>)<v>\d+</v>', r'\g<1><v>%d</v>'%SI_SV_NAME, new63)
new63=re.sub(r'(<c r="C63"[^>]*>)<v>\d+</v>', r'\g<1><v>0</v>', new63)
new63=re.sub(r'<c r="D63"( s="\d+")?/>', lambda mm:'<c r="D63"%s><v>0</v></c>'%(mm.group(1) or ''), new63)
new63=re.sub(r'<c r="E63"( s="\d+")?/>', lambda mm:'<c r="E63"%s><v>0</v></c>'%(mm.group(1) or ''), new63)
new63=re.sub(r'(<c r="G63"[^>]*t="s"[^>]*>)<v>\d+</v>', r'\g<1><v>%d</v>'%SI_SV_ST, new63)
assert new63!=row63
s3=s3.replace(row63,new63)
report.append(('G','Лист «Сверка ТХ.2»: две строки стола 1200 объединены в одну — СО 10 шт = ВОР 10 шт, разница 0, статус «сходится». Расхождение «ВОР больше СО на 1» было артефактом двух написаний.'))

# ---- H. журнал в лист «Правки» -------------------------------------------
last=int(re.findall(r'<row r="(\d+)"', s4)[-1])
rows_xml=[]
rn=last
for spec_row, col, was, became, what in sorted(log, key=lambda x:(x[0], x[1])):
    rn+=1
    cells=['<c r="A%d" s="119"><v>%d</v></c>'%(rn,spec_row),
           '<c r="B%d" s="119" t="s"><v>%d</v></c>'%(rn,S(col))]
    cells.append('<c r="C%d" s="119"/>'%rn if was=='' else '<c r="C%d" s="119" t="s"><v>%d</v></c>'%(rn,S(was)))
    cells.append('<c r="D%d" s="119" t="s"><v>%d</v></c>'%(rn,S(became)))
    cells.append('<c r="E%d" s="119" t="s"><v>%d</v></c>'%(rn,S(what)))
    rows_xml.append('<row r="%d" spans="1:5" x14ac:dyDescent="0.25">%s</row>'%(rn,''.join(cells)))
s4=s4.replace('</sheetData>', ''.join(rows_xml)+'</sheetData>')
s4=s4.replace('<dimension ref="A1:E%d"/>'%last, '<dimension ref="A1:E%d"/>'%rn)
s4=s4.replace('<autoFilter ref="A2:E%d"/>'%last, '<autoFilter ref="A2:E%d"/>'%rn)
report.append(('H','В лист «Правки» дописано %d записей (строки %d–%d) — по конвенции самой книги.'%(len(log), last+1, rn)))

# ---- дописать новые общие строки и поправить счётчики --------------------
if new_si:
    add=''.join('<si><t xml:space="preserve">%s</t></si>'%esc(t) for t in new_si)
    ss=ss.replace('</sst>', add+'</sst>')
mc=re.search(r'count="(\d+)" uniqueCount="(\d+)"', ss)
old_count=int(mc.group(1)); old_uni=int(mc.group(2))
str_cells=sum(len(re.findall(r'<c [^>]*t="s"', open(P('xl/worksheets/sheet%d.xml'%n),encoding='utf-8').read() if n==2 else {1:s1,3:s3,4:s4}.get(n,''))) for n in (1,2,3,4))
ss=ss.replace('count="%d" uniqueCount="%d"'%(old_count,old_uni),
              'count="%d" uniqueCount="%d"'%(str_cells, old_uni+len(new_si)))

open(P('xl/sharedStrings.xml'),'w',encoding='utf-8').write(ss)
open(P('xl/worksheets/sheet1.xml'),'w',encoding='utf-8').write(s1)
open(P('xl/worksheets/sheet3.xml'),'w',encoding='utf-8').write(s3)
open(P('xl/worksheets/sheet4.xml'),'w',encoding='utf-8').write(s4)

# ---- пересобрать книгу (порядок записей как в оригинале) ----------------
if os.path.exists(OUT): os.remove(OUT)
with zipfile.ZipFile(SRC) as zin, zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        zout.writestr(item, open(P(item.filename),'rb').read())

print('НОВЫХ ОБЩИХ СТРОК:', len(new_si), '| строковых ячеек:', str_cells, '(было %d)'%old_count)
print('ЗАПИСЕЙ В ЖУРНАЛ:', len(log))
print()
for k,t in report: print('[%s] %s'%(k,t))
print()
print('ФАЙЛ:', OUT, os.path.getsize(OUT)//1024, 'КБ  (исходник %d КБ)'%(os.path.getsize(SRC)//1024))
