from zipfile import ZipFile
from lxml import etree
from collections import Counter

files = ['DangGiaHuy_DeCuong_IoT_DaChinhSua.docx', 'DangGiaHuy_DeCuong_IoT_DaChinhSua2.docx']
ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'}
texts, structures = [], []
for filename in files:
    with ZipFile(filename) as package:
        root = etree.fromstring(package.read('word/document.xml'))
        texts.append([''.join(p.itertext()).replace('−', '-') for p in root.xpath('//w:body//w:p', namespaces=ns)])
        math = root.xpath('//m:oMath', namespaces=ns)
        def shape(node):
            return (etree.QName(node).localname, tuple(sorted(node.attrib.items())),
                    (node.text or '').replace('−', '-'), tuple(shape(child) for child in node))
        structures.append([shape(m) for m in math])
        print(filename, 'paragraphs', len(texts[-1]), 'tables', len(root.xpath('//w:tbl', namespaces=ns)), 'equations', len(math))
        print('media', [(name, len(package.read(name))) for name in package.namelist() if name.startswith('word/media/')])
        print('math tags', Counter(etree.QName(n).localname for m in math for n in m.iter()))
        print('spacing', Counter(root.xpath('//w:pPr/w:spacing/@w:line', namespaces=ns)))
        print('drawings', len(root.xpath('//w:drawing', namespaces=ns)))
        print('image2 relationship references', [(name, package.read(name).count(b'rId15'))
              for name in package.namelist() if name.endswith('.xml') and b'rId15' in package.read(name)])
        print('body image references', root.xpath('//@*[local-name()="embed"]'))
        print('page metadata', package.read('docProps/app.xml').decode())
print('Text equal after minus normalization:', texts[0] == texts[1])
print('Math structure equal after minus normalization:', structures[0] == structures[1])
core = {'f', 'num', 'den', 'sSub', 'sSup', 'sSubSup', 'sub', 'sup', 'acc', 'rad', 'deg', 'nary', 'd', 'e', 'bar', 'limLow', 'lim'}
def semantic_parts(filename):
    with ZipFile(filename) as package:
        root = etree.fromstring(package.read('word/document.xml'))
    return [[(etree.QName(n).localname, ''.join(n.itertext()).replace('−', '-'))
             for n in math.iter() if etree.QName(n).localname in core]
            for math in root.xpath('//m:oMath', namespaces=ns)]
print('Core mathematical structure and content equal:', semantic_parts(files[0]) == semantic_parts(files[1]))
