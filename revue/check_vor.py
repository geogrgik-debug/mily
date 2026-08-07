# -*- coding: utf-8 -*-
"""РЕВЬЮ (без правок): проверка формул в колонке E сводного файла ВОР."""
import openpyxl, re, json
p='/root/.claude/uploads/a6572ada-bc7d-590d-9f64-07c4882f0066/f840d37a-___________.xlsx'
wb=openpyxl.load_workbook(p, data_only=True, read_only=True)
rows=[[c.value for c in r] for r in wb['Sheet1'].iter_rows()]
def s(x): return '' if x is None else re.sub(r'\s+',' ',str(x)).strip()

# текущий ВОР для каждой строки
vor=[None]*len(rows); cur=None
for i,v in enumerate(rows):
    m=re.search(r'ВОР\s*(\d\d-\d\d-\d\d)', s(v[0])+' '+s(v[1]))
    if m and 'Ведомость' in (s(v[0])+s(v[1])): cur=m.group(1)
    vor[i]=cur

def prep(f):
    """привести запись формулы к виду, который можно посчитать"""
    t=f.strip()
    t=t.replace(' ',' ').replace('−','-').replace('–','-').replace('—','-')
    t=t.replace('×','*').replace('·','*')
    t=re.sub(r'[xXхХ]', '*', t)          # латинская и кириллическая «икс» как знак умножения
    t=re.sub(r'(\d),(\d)', r'\1.\2', t)  # десятичная запятая
    t=t.replace(' ','')
    t=re.sub(r'(\d|\))\(', r'\1*(', t)   # 2(3) -> 2*(3)
    t=re.sub(r'\)(\d)', r')*\1', t)      # (3)2 -> (3)*2
    return t

bad_parse=[]; mismatch=[]; ok=0; nof=0
for i,v in enumerate(rows):
    f=s(v[4]); q=v[3]
    if not f: nof+=1; continue
    if not isinstance(q,(int,float)): continue
    if not re.search(r'\d', f): continue
    if re.search(r'[А-Яа-яA-Za-z]{2,}', f):    # текстовые пояснения, не формула
        continue
    t=prep(f)
    if not re.fullmatch(r'[\d\.\+\-\*/\(\)]+', t):
        bad_parse.append((i+1, vor[i], s(v[1])[:46], f, q, 'непонятные символы')); continue
    try:
        val=eval(t, {'__builtins__':{}}, {})
    except ZeroDivisionError:
        bad_parse.append((i+1, vor[i], s(v[1])[:46], f, q, 'деление на ноль')); continue
    except Exception as e:
        bad_parse.append((i+1, vor[i], s(v[1])[:46], f, q, 'не считается: %s'%type(e).__name__)); continue
    if q==0 and val==0: ok+=1; continue
    denom=max(abs(q),abs(val),1e-9)
    if abs(val-q)/denom > 0.005:
        mismatch.append((i+1, vor[i], s(v[1])[:46], f, q, round(val,4), round(val-q,4)))
    else: ok+=1

print('=== ПРОВЕРКА ФОРМУЛ (колонка E) ПРОТИВ ОБЪЁМА (колонка D) ===')
print('формул проверено: %d | сошлось: %d | НЕ сошлось: %d | не разобрано: %d | строк без формулы: %d'
      % (ok+len(mismatch)+len(bad_parse), ok, len(mismatch), len(bad_parse), nof))
print()
print('--- ФОРМУЛА НЕ СХОДИТСЯ С ОБЪЁМОМ (%d) ---' % len(mismatch))
print('%-7s %-10s %-46s %-30s %14s %14s %12s' % ('строка','ВОР','наименование','формула','в файле','по формуле','разница'))
for m in mismatch:
    print('%-7d %-10s %-46s %-30s %14s %14s %12s' % m)
print()
print('--- ФОРМУЛА НЕ РАЗБИРАЕТСЯ (%d) ---' % len(bad_parse))
for b in bad_parse:
    print('стр.%-6d %-10s %-46s «%s» -> %s' % (b[0],b[1],b[2],b[3][:44],b[5]))
json.dump({'mismatch':mismatch,'bad':bad_parse}, open('vor_formulas.json','w'), ensure_ascii=False, indent=1)
