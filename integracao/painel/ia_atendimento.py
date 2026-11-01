"""Preparação do atendimento: importação, referências e triagem local."""
import hashlib
import re
import unicodedata


def normalizar(texto):
    return ''.join(c for c in unicodedata.normalize('NFD',str(texto).lower()) if unicodedata.category(c)!='Mn')


def anonimizar(texto, nomes=()):
    text=str(texto or '')
    for nome in sorted(set(str(n).strip() for n in nomes if str(n).strip() and normalizar(n).strip() not in ('sonifarma','farmacia sonifarma','sonifarma farmacia','atendimento sonifarma')),key=len,reverse=True):
        text=re.sub(r'(?<!\w)'+re.escape(nome)+r'(?!\w)','[NOME]',text,flags=re.I)
    text=re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b','[EMAIL]',text)
    text=re.sub(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b','[DOCUMENTO]',text)
    text=re.sub(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b','[DOCUMENTO]',text)
    text=re.sub(r'(?<!\w)(?:\+?55[\s-]?)?\(?[1-9]\d\)?[\s-]?\d{4,5}[\s-]?\d{4}(?!\w)','[TELEFONE]',text)
    text=re.sub(r'\b\d{5}-\d{3}\b','[CEP]',text)
    text=re.sub(r'(?im)^(?:meu\s+)?(?:endere[cç]o|rua|avenida|av\.)\b[^\n]*','[ENDEREÇO]',text)
    return text.strip()


def triagem(texto):
    t=normalizar(texto)
    if re.search(r'dor no peito|falta de ar|nao consigo respirar|desmai|intoxic|suicid|me matar',t):return 'urgencia'
    if t.strip(' .!') in ('stop','pare','parar','sair') or re.search(r'nao (?:quero|desejo) (?:mais )?receber (?:mensagens|ofertas)|pare de (?:mandar|enviar)',t):return 'descadastro'
    if re.search(r'\b[0-9]+(?:[,.][0-9]+)?\s*(?:mg|mcg|ml|gotas|comprimidos|capsulas)\b',t):return 'farmaceutico'
    if re.search(r'\b(?:medicamento|remedio|diabetes|hipertensao|posologia)\b',t):return 'farmaceutico'
    if re.search(r'\b(?:dor|febre|enjoo|tontura|vomito|diarreia|sintoma|coceira|receita|exame)\b|pressao (?:alta|baixa)|posso (?:tomar|usar)|qual (?:a )?(?:dose|dosagem)|quant[oa]s? (?:comprimidos|gotas)|intera[cç]|efeito[s]? colatera|contraindica|gestante|gravida|amament|crianca|alerg|mudar (?:a )?dose',t):return 'farmaceutico'
    return 'operacional'


def resposta_encaminhamento(tipo):
    if tipo=='urgencia':return 'Para sua segurança, procure atendimento de urgência agora. Se necessário, ligue 192 (SAMU). Vou encaminhar seu atendimento à equipe.'
    if tipo=='descadastro':return 'Entendido. Vou encaminhar seu pedido à equipe para registrar o bloqueio de mensagens.'
    return 'Esse assunto precisa da avaliação do nosso farmacêutico. Vou encaminhar seu atendimento para que você receba a orientação adequada.'


def importar_txt(texto, atendentes):
    nomes={normalizar(n.strip()) for n in atendentes if isinstance(n,str) and n.strip()}
    if not nomes:raise ValueError('Informe os nomes dos atendentes como aparecem no arquivo')
    messages=[];current=None
    pattern=re.compile(r'^\[?\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?\]?\s*(?:[-–]\s*)?([^:\n]+):\s*(.*)$')
    for line in texto.replace('\ufeff','').replace('\u200e','').replace('\u202f',' ').splitlines():
        m=pattern.match(line)
        if m:
            current={'autor':m[1].strip(),'texto':m[2].strip()};messages.append(current)
        elif current:current['texto']+='\n'+line
    if not messages:raise ValueError('Formato não reconhecido. Use a exportação .txt do WhatsApp, sem mídia')
    autores=[m['autor'] for m in messages]
    if len({normalizar(n) for n in autores if normalizar(n) not in nomes})>1:raise ValueError('Use uma conversa individual e informe todos os atendentes presentes no arquivo')
    candidatos=[];pergunta=[];resposta=[]
    def flush():
        if pergunta and resposta:
            q=anonimizar('\n'.join(pergunta),autores);a=anonimizar('\n'.join(resposta),autores)
            if q and a and triagem(q)=='operacional' and triagem(a)=='operacional' and len(q)<=2000 and len(a)<=4000:
                candidatos.append({'pergunta':q,'resposta':a,'digest':hashlib.sha256(('estilo\n'+q+'\n'+a).encode()).hexdigest()})
    for m in messages:
        if re.search(r'm[ií]dia oculta|imagem ocult|[áa]udio ocult|imagem omitida|audio omitido|arquivo anexado|<media omitted>|<attached:|<anexado:',m['texto'],re.I):continue
        if normalizar(m['autor']) in nomes:
            if pergunta:resposta.append(m['texto'])
        else:
            if resposta:flush();pergunta=[];resposta=[]
            pergunta.append(m['texto'])
    flush()
    return candidatos


def selecionar_exemplos(pergunta, rows):
    stop={'para','como','qual','voce','voces','esta','esse','essa','aqui','bom','dia','ola','uma','com','por','que','das','dos','de','do','da','e','o','a'}
    tokens=lambda t:{x for x in re.findall(r'[a-z0-9]+',normalizar(t)) if len(x)>2 and x not in stop}
    q=tokens(pergunta)
    score=lambda row:len(q&tokens(row['pergunta']))/max(1,len(q|tokens(row['pergunta'])))
    return sorted([r for r in rows if r['tipo']=='estilo' and score(r)>0],key=score,reverse=True)[:4]
