# -*- coding: utf-8 -*-
"""ПК-оборудование из спецификации + где купить в РФ. Исходные файлы не изменяются."""
import openpyxl, re
from collections import defaultdict
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
SRC='/root/.claude/uploads/a6572ada-bc7d-590d-9f64-07c4882f0066/330c2672-SPEC_IOS_TH_ITOGOVIY_FILE2.xlsx'
wb0=openpyxl.load_workbook(SRC, data_only=True); ws0=wb0['Спецификация']
def s(x): return '' if x is None else re.sub(r'\s+',' ',str(x)).strip()

# собираем строки по каждой ПК-позиции
def collect(pat, art=None):
    p=re.compile(pat, re.I); rows=[]; q=0
    for r in ws0.iter_rows(min_row=2):
        nm=s(r[1].value); a=s(r[2].value)
        if not isinstance(r[6].value,(int,float)): continue
        if not p.search(nm): continue
        if art and art.lower() not in a.lower(): continue
        rows.append(r[0].row); q+=r[6].value
    return q, rows

ITEMS=[
 # (позиция в спец., модель/что искать, кол-во-паттерн, арт, цена от, цена до, ссылки, примечание)
 ('Персональный компьютер (моноблок) с периферией, ИБП, сетевой фильтр',
  r'персональный компьютер \(моноблок\)', None,
  45000, 120000,
  ['DNS — моноблоки для офиса: https://www.dns-shop.ru/catalog/recipe/31262d1866ae2669/dla-ofisa/',
   'DNS — моноблоки для бизнеса: https://www.dns-shop.ru/catalog/recipe/b38129eae9da6653/dla-biznesa/',
   'DNS — весь каталог моноблоков: https://www.dns-shop.ru/catalog/17a8936316404e77/monobloki/'],
  'Модель в спецификации НЕ ЗАДАНА — только «моноблок с периферией». Периферия (клавиатура, мышь) в отдельные строки не вынесена, идёт в составе. Нужно выбрать модель и зафиксировать её в спецификации, иначе цена не защищается.'),

 ('МФУ (копир/сканер/принтер), цветная печать, А4 и А3',
  r'многофункциональное устройство.*цветная печать', None,
  90000, 350000,
  ['DNS — цветные лазерные МФУ А3: https://www.dns-shop.ru/catalog/recipe/38b13e633dec8dd4/cvetnye-a3/',
   'Ситилинк — лазерные МФУ А3: https://www.citilink.ru/catalog/mfu--mfu-lazernye-a3/',
   'DNS — все лазерные МФУ: https://www.dns-shop.ru/catalog/17a8df6816404e77/lazernye-mfu/'],
  'Модель не задана. Цветной лазерный А3 — самая дорогая категория МФУ, разброс огромный.'),

 ('МФУ (копир/сканер/принтер), А4 и А3, с печатью фотографий',
  r'многофункциональное устройство.*фотограф', None,
  90000, 350000,
  ['DNS — цветные лазерные МФУ А3: https://www.dns-shop.ru/catalog/recipe/38b13e633dec8dd4/cvetnye-a3/',
   'Ситилинк — МФУ: https://www.citilink.ru/catalog/computers_and_notebooks/monitors_and_office/mfu/'],
  'Модель не задана. «Печать фотографий» обычно означает струйный или цветной лазерный с фотокачеством — уточнить требование.'),

 ('Принтер, формат А4', r'^принтер, формат', None,
  8000, 40000,
  ['Ситилинк — принтеры лазерные: https://www.citilink.ru/catalog/printery-lazernye/',
   'DNS — принтеры: https://www.dns-shop.ru/catalog/17a89aec16404e77/printery/'],
  'Модель не задана.'),

 ('Принтер лазерный Kyocera ECOSYS P3045dn', r'принтер лазерный', 'ECOSYS P3045dn',
  23048, 115000,
  ['ForOffice — 23 048 ₽: https://www.foroffice.ru/products/description/130591.html',
   'NIX: https://www.nix.ru/autocatalog/printers_mfu_kyocera/Kyocera-Ecosys-P3045dn-A4-45-str-min-512Mb-LCD-USB20-setevoj-dvust-pechat_291857.html',
   'OfiTrade: https://www.ofitrade.ru/cat/printers/laser/kyocera-ecosys-p3045dn-1102t93nl0/'],
  'ВНИМАНИЕ: у части поставщиков помечен как снятый с производства. Перед закупкой проверить наличие или подобрать замену.'),

 ('Монитор Hikvision DS-D5027FN, 27"', r'монитор', 'DS-D5027FN',
  13673, 57690,
  ['Hikvision24 — 13 673 ₽: https://hikvision24.ru/aksessuary/monitory/monitor-ds-d5027fn/',
   'ИНФОТЕХ — 9 907 ₽: https://i-teh.com/catalog/monitory/ds_d5027fn/',
   'DSSL — 17 330 ₽: https://www.dssl.ru/products/ds-d5027fn-monitor-dlya-videonablyudeniya/',
   'ВИДЕОГЛАЗ: https://videoglaz.ru/kompyuternye-monitory-lcd-tft/hikvision/hikvision-ds-d5027fn'],
  'Разброс цен в 4 раза между магазинами на одну и ту же модель — обязательно сравнивать.'),

 ('Монитор Hikvision DS-D5032QE, 31.5"', r'монитор', 'DS-D5032QE',
  25439, 35090,
  ['Telecamera — 25 439 ₽: https://www.telecamera.ru/catalog/Domofony/Videodomofony/Monitory/HIKVISION/DS_D5032QE.htm',
   'Регард: https://www.regard.ru/product/440318/monitor-hikvision-32-ds-d5032qe',
   'DSSL: https://www.dssl.ru/products/ds-d5032qe-monitor-dlya-videonablyudeniya/'],
  'В спецификации написано 32", по факту диагональ 31.5".'),

 ('Монитор Hikvision DS-D5022QE-B, 21.5"', r'монитор', 'DS-D5022QE-B',
  17853, 25485,
  ['Hikvision24 — 17 993 ₽: https://hikvision24.ru/aksessuary/monitory/monitor-ds-d5022qe-b/',
   'Интемс — 22 790 ₽: https://securityrussia.com/cctv/monitory/67407',
   'ЭТМ iPRO: https://www.etm.ru/cat/nn/4121465',
   'DSSL: https://www.dssl.ru/products/ds-d5022qe-b/'],
  ''),

 ('Монитор Dell P2419H, 23.8"', r'^монитор$', 'DELL P2419H',
  0, 62530,
  ['DNS: https://www.dns-shop.ru/product/c9ab866093973330/238-monitor-dell-p2419h-cernyj/',
   'Ситилинк: https://www.citilink.ru/product/monitor-dell-p2419h-23-8-chernyi-2419-2392-1074862/',
   'KNS: https://www.kns.ru/product/monitor-dell-p2419h/'],
  'ВНИМАНИЕ: Dell ушёл из России, модель старая (2019 г.). В наличии почти нигде нет, цены сильно скачут. Заложить замену на актуальную модель.'),

 ('Кронштейн настенный Kromax TECHNO-11 BLACK для мониторов 32"',
  r'кронштейн', 'TECHNO-11',
  1500, 3500,
  ['Kromax (производитель): https://www.kromax.ru/produce/plasma/5103/',
   'KNS: https://www.kns.ru/product/kronshtein-kromax-techno-11-black/',
   'Яндекс.Маркет — кронштейны: https://market.yandex.ru/category/kronshteyny-dlya-monitorov'],
  'ВАЖНО: TECHNO-11 рассчитан на экраны 10"–32" и до 15 кг. Мониторы в спецификации — 31.5", то есть на самой границе. Проверить вес монитора DS-D5032QE.'),

 ('Жёсткий диск WD Purple Pro WD141PURP, 14 ТБ', r'жесткий диск', 'WD141PURP',
  50618, 50618,
  ['DNS: https://www.dns-shop.ru/product/db9b6068eb79ed20/14-tb-zestkij-disk-wd-purple-pro-wd141purp/',
   'Ситилинк: https://www.citilink.ru/product/zhestkii-disk-wd-purple-pro-wd141purp-14tb-hdd-sata-iii-3-5-1744120/',
   'NIX — 50 618 ₽: https://www.nix.ru/autocatalog/hdd_western_digital/HDD-14-Tb-SATA-6Gb-s-Western-Digital-Purple-Pro-WD141PURP-35_547628.html',
   'Тинко: https://www.tinko.ru/catalog/product/301644/'],
  'Диск под видеонаблюдение. Есть более новый аналог WD142PURP — уточнить, чем комплектовать.'),

 ('Сервер DEPO Storm 1420Q1', r'сервер depo storm 1420', None,
  0, 0,
  ['ДЕПО Компьютерс (производитель, конфигуратор): https://www.depo.ru/catalog/servery/',
   'РуИТС: https://ruits.ru/catalog/servernoe-oborudovanie/servery-depo/server-depo-storm-1420q1',
   'CSV: http://www.c-s-v.ru/products/110/1790/',
   'ГК Хайтек: https://gk-ht.ru/catalog/servery/rossiyskie-servery/servery-depo/'],
  'Открытых цен нет — только по запросу у дилера. Конфигурация в спецификации расписана полностью, отправлять её в конфигуратор ДЕПО как есть.'),

 ('Сервер DEPO Storm 3450Z1', r'сервер depo storm 3450', None,
  0, 0,
  ['ДЕПО Компьютерс (конфигуратор): https://www.depo.ru/catalog/servery/',
   'ГК Хайтек: https://gk-ht.ru/catalog/servery/rossiyskie-servery/servery-depo/',
   'ServerMall: https://servermall.ru/special/statecompany/servernoe-oborudovanie-depo/'],
  'Только по запросу. Конфигурация тяжёлая: 2× Xeon Silver 4208, 128 ГБ, Windows Server 2019 Std — это самая дорогая ИТ-позиция из непроценённых.'),

 ('Ноутбук', r'^ноутбук', None,
  40000, 150000,
  ['DNS — ноутбуки: https://www.dns-shop.ru/catalog/17a892f816404e77/noutbuki/',
   'Ситилинк — ноутбуки: https://www.citilink.ru/catalog/noutbuki/'],
  'В спецификации только «Ноутбук Р=0,5 кВт, 220 В» — ни модели, ни характеристик. Требует уточнения.'),

 ('Проектор Acer X1328WHn', r'проектор acer x1328whn', None,
  54890, 75590,
  ['Регард — 55 540 ₽: https://www.regard.ru/product/710326/proektor-acer-x1328whn',
   'Ситилинк — 62 790 ₽: https://www.citilink.ru/product/proektor-acer-x1328whn-dlp-5000lm-ls-20000-1-6000chas-1xhdmi-2-7kg-2023983/',
   'Pult.ru: https://www.pult.ru/product/proektor-acer-x1328whn'],
  ''),

 ('Карт-принтер HID Fargo C50', r'карт-принтер', 'Fargo C50',
  104595, 159286,
  ['Элайтс/Смарткод — 104 595 ₽: https://smartcode.ru/shtrihkodirovanie_i_identifikatsiya/printery_pechati_plastikovyh_kart/fargo_c50_51712',
   'ForOffice: https://www.foroffice.ru/products/description/71773.html',
   'Fargo.ru (офиц.): https://fargo.ru/products/product/fargo-51981-printer-plastikovykh-kart-c50',
   'Баргас — 159 286 ₽ (с лентой YMCKO): https://www.bargas.ru/catalog/printery_dlya_pechati_plastikovykh_kart/printery_fargo/1781/'],
  'Цена сильно зависит от комплекта: с лентой YMCKO дороже на ~55 тыс. Уточнить, входит ли лента.'),

 ('Пластиковые карты EM-Marine TK4100 ISO, 125 кГц, номерные',
  r'пластиковая карта', 'TK 4100',
  14, 20,
  ['ЗКТeco-store — от 14 ₽/шт: https://zkteco-store.ru/shop/rfid-karta-em-marine-iso-125-kgc-s-nomerom/',
   'US-PLAST: https://us-plast.ru/product/proximity-karta-em-marine-tk4100-pod-pechat/',
   'SmartCardShop: https://smartcardshop.ru/catalog/smart-karty-em-marine/karta-tonkaya-em-marine-tk4100-iso/',
   'СтранаКарт: https://stranakart.com/katalog/rfid-karty-s-chipom/plastikovye-rfid-karty-s-chipom-em-marine-iso-em4200-tk4100'],
  'Цена за штуку, продаются упаковками по 200. 500 шт = 3 упаковки.'),

 ('Телевизор на консоли', r'телевизор на консоли', None,
  30000, 90000,
  ['DNS — телевизоры: https://www.dns-shop.ru/catalog/17a8ac0f16404e77/televizory/',
   'Ситилинк — телевизоры: https://www.citilink.ru/catalog/televizory/'],
  'Модель не задана, только мощность 0,2 кВт. Консоль (кронштейн) в отдельную строку не вынесена — уточнить, входит ли.'),

 ('ИБП SKAT-UPS 1000 RACK, 1 кВА / 0,9 кВт, 2U',
  r'источник бесперебойного питания 1000', 'SKAT-UPS 1000 RACK',
  71760, 76591,
  ['СКАТ (производитель): https://skat-ups.ru/catalog/ups/skat-ups-1000-rack/',
   'UPS-Mag: https://www.ups-mag.ru/catalog/ups/bastion/skat-ups-1000-rack',
   'Сатро-Паладин — 73 000 ₽: https://satro-paladin.com/catalog/product/11230/',
   'Ритм-ИТ — 76 591 ₽: https://www.ritm-it.ru/ups/bastion/sistemi-besperebojnogo-elektropitaniya-92701/istochniki-besperebojnogo-pitaniya-104909/SKAT-UPS-1000-VAR-V-RACK-401037.htm'],
  ''),
]

F='Arial'
H=Font(name=F,size=10,bold=True,color='FFFFFF'); HF=PatternFill('solid',fgColor='1F3864')
B=Font(name=F,size=10,bold=True); Nf=Font(name=F,size=10)
LINK=Font(name=F,size=9,color='0563C1')
WARN=PatternFill('solid',fgColor='FFE699'); BAD=PatternFill('solid',fgColor='FFC7CE')
thin=Side(style='thin',color='BFBFBF'); BRD=Border(left=thin,right=thin,top=thin,bottom=thin)
MONEY='#,##0 ₽;[Red](#,##0);-'
wb=openpyxl.Workbook(); ws=wb.active; ws.title='ПК-оборудование'
ws['A1']='КОМПЬЮТЕРНОЕ ОБОРУДОВАНИЕ ИЗ СПЕЦИФИКАЦИИ — ГДЕ КУПИТЬ В РОССИИ'
ws['A1'].font=Font(name=F,size=14,bold=True)
ws['A2']='Цены — ориентир на август 2026 по открытым прайсам магазинов. Это НЕ коммерческие предложения: перед закупкой запрашивать КП. Исходная спецификация не изменялась.'
ws['A2'].font=Font(name=F,size=9,italic=True)
cols=['№','Позиция','Кол-во','Строки в «Спецификации»','Цена от, ₽','Цена до, ₽','Сумма от, ₽','Сумма до, ₽','Где купить','На что обратить внимание']
widths=[5,46,8,26,12,12,15,15,72,60]
r=4
for j,(c,w) in enumerate(zip(cols,widths),1):
    cell=ws.cell(row=r,column=j,value=c); cell.font=H; cell.fill=HF; cell.border=BRD
    cell.alignment=Alignment(wrap_text=True,vertical='center',horizontal='center')
    ws.column_dimensions[get_column_letter(j)].width=w
ws.row_dimensions[r].height=32; ws.freeze_panes=ws.cell(row=r+1,column=1)
r+=1; first=r; n=0
for name,pat,art,lo,hi,links,note in ITEMS:
    q,rws=collect(pat,art)
    if not rws: continue
    n+=1
    ws.cell(row=r,column=1,value=n); ws.cell(row=r,column=2,value=name)
    ws.cell(row=r,column=3,value=q)
    ws.cell(row=r,column=4,value=', '.join(map(str,rws[:14]))+(' …' if len(rws)>14 else ''))
    ws.cell(row=r,column=5,value=lo if lo else None); ws.cell(row=r,column=6,value=hi if hi else None)
    ws.cell(row=r,column=7,value='=IF(E%d="",\"\",C%d*E%d)'%(r,r,r))
    ws.cell(row=r,column=8,value='=IF(F%d="",\"\",C%d*F%d)'%(r,r,r))
    ws.cell(row=r,column=9,value='\n'.join(links)); ws.cell(row=r,column=9).font=LINK
    ws.cell(row=r,column=10,value=note)
    if 'ВНИМАНИЕ' in note or 'ВАЖНО' in note: ws.cell(row=r,column=10).fill=BAD
    elif note: ws.cell(row=r,column=10).fill=WARN
    if not lo:
        ws.cell(row=r,column=5,value='по запросу'); ws.cell(row=r,column=6,value='по запросу')
        ws.cell(row=r,column=5).fill=BAD; ws.cell(row=r,column=6).fill=BAD
    r+=1
last=r-1
ws.cell(row=r,column=2,value='ИТОГО ориентировочно').font=B
ws.cell(row=r,column=3,value='=SUM(C%d:C%d)'%(first,last)).font=B
ws.cell(row=r,column=7,value='=SUM(G%d:G%d)'%(first,last)).font=B
ws.cell(row=r,column=8,value='=SUM(H%d:H%d)'%(first,last)).font=B
for row in ws.iter_rows(min_row=first,max_row=r,min_col=1,max_col=10):
    for c in row:
        if not c.font.bold and c.font.color is None: c.font=Nf
        c.border=BRD; c.alignment=Alignment(wrap_text=(c.column in (2,4,9,10)),vertical='top')
        if c.column in (5,6,7,8) and isinstance(c.value,(int,float)): c.number_format=MONEY
        if c.column in (7,8) and isinstance(c.value,str) and c.value.startswith('='): c.number_format=MONEY
ws.cell(row=r,column=7).number_format=MONEY; ws.cell(row=r,column=8).number_format=MONEY
wb.save('ПК_оборудование_где_купить.xlsx')
print('готово, позиций:', n)
for name,pat,art,lo,hi,links,note in ITEMS:
    q,rws=collect(pat,art)
    print('  %-58s %6s шт  строк %d' % (name[:58], q, len(rws)))
