# -*- coding: utf-8 -*-
"""РЕВЬЮ (без правок): орфография и мусор в тексте листа «Спецификация»."""
import openpyxl, re, json
from collections import Counter, defaultdict
SRC='/root/.claude/uploads/a6572ada-bc7d-590d-9f64-07c4882f0066/330c2672-SPEC_IOS_TH_ITOGOVIY_FILE2.xlsx'
wb=openpyxl.load_workbook(SRC, data_only=True)
ws=wb['Спецификация']
COL={1:'A — № п/п',2:'B — Наименование',3:'C — Тип, марка',4:'D — Код',5:'E — Поставщик',
     6:'F — Ед. изм.',7:'G — Кол-во',8:'H — Цена',9:'I — Сумма',10:'J — Масса',11:'K — Примечание'}

# латиница, похожая на кириллицу
LAT2CYR={'a':'а','c':'с','e':'е','o':'о','p':'р','x':'х','y':'у','A':'А','B':'В','C':'С','E':'Е',
         'H':'Н','K':'К','M':'М','O':'О','P':'Р','T':'Т','X':'Х','y':'у','i':'і'}
mixed=defaultdict(list); broken=defaultdict(list); dbl=defaultdict(list); edge=defaultdict(list)

for row in ws.iter_rows(min_row=2):
    for c in row:
        v=c.value
        if not isinstance(v,str) or not v.strip(): continue
        col=COL.get(c.column, str(c.column))
        # 1. смешанные алфавиты внутри одного слова
        for w in re.findall(r'[A-Za-zА-Яа-яЁё]{2,}', v):
            has_cyr=bool(re.search(r'[А-Яа-яЁё]', w)); has_lat=bool(re.search(r'[A-Za-z]', w))
            if has_cyr and has_lat:
                mixed[(col,w)].append(c.row)
        # 2. разрыв внутри слова: «Торгов ая», «груз а», «Авто- матика»
        for m in re.finditer(r'[а-яё]{2,}[- ][а-яё]{1,3}\b', v):
            frag=m.group(0)
            if re.match(r'^[а-яё]+ (ая|ое|ые|ий|ая|ой|ов|ка|ки|ца|ая)$', frag) or '- ' in frag:
                broken[(col,frag)].append(c.row)
        # 3. двойные пробелы
        if '  ' in v: dbl[(col,re.sub(r'\s+',' ',v)[:52])].append(c.row)
        # 4. пробел в начале/конце
        if v!=v.strip(): edge[(col,v.strip()[:52])].append(c.row)

def dump(title, d, limit=40):
    print('\n=== %s — %d разных значений, %d ячеек ===' % (title, len(d), sum(len(x) for x in d.values())))
    for (col,txt),rws in sorted(d.items(), key=lambda x:-len(x[1]))[:limit]:
        print('  ×%-4d %-18s «%s»' % (len(rws), col, txt))
        print('        строки: %s%s' % (', '.join(map(str,rws[:12])), ' …' if len(rws)>12 else ''))

dump('ЛАТИНИЦА ВНУТРИ РУССКОГО СЛОВА', mixed)
dump('РАЗРЫВ ВНУТРИ СЛОВА', broken)
dump('ДВОЙНЫЕ ПРОБЕЛЫ', dbl, 20)
dump('ПРОБЕЛ В НАЧАЛЕ/КОНЦЕ ЯЧЕЙКИ', edge, 20)
