"""Cliente da WhatsApp Cloud API; segredos permanecem no servidor local."""
import hashlib
import hmac
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request


class MetaError(Exception):
    def __init__(self, message, code='', uncertain=False):
        super().__init__(message)
        self.code = str(code)
        self.uncertain = uncertain


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def settings(values):
    return {k: str(values.get('WHATSAPP_' + k, '')).strip() for k in (
        'ENABLED', 'GRAPH_VERSION', 'PHONE_NUMBER_ID', 'WABA_ID', 'ACCESS_TOKEN',
        'APP_SECRET', 'WEBHOOK_VERIFY_TOKEN')}


def readiness(cfg):
    missing = [k for k in ('GRAPH_VERSION', 'PHONE_NUMBER_ID', 'WABA_ID', 'ACCESS_TOKEN') if not cfg.get(k)]
    if cfg.get('GRAPH_VERSION') and not re.fullmatch(r'v[0-9]{1,3}\.[0-9]{1,2}', cfg['GRAPH_VERSION']):
        missing.append('GRAPH_VERSION válida')
    for key in ('PHONE_NUMBER_ID', 'WABA_ID'):
        if cfg.get(key) and not re.fullmatch(r'[0-9]{5,30}', cfg[key]):
            missing.append(key + ' válido')
    enabled = cfg.get('ENABLED', '').lower() in ('true', '1', 'sim')
    return dict(configurado=not missing, habilitado=enabled, faltantes=missing,
                webhook_configurado=bool(cfg.get('APP_SECRET') and cfg.get('WEBHOOK_VERIFY_TOKEN')))


def graph(cfg, path, payload=None, params=None):
    state = readiness(cfg)
    if not state['configurado'] or not state['habilitado']:
        raise MetaError('Configure e habilite a API no servidor antes de continuar.')
    url = 'https://graph.facebook.com/' + cfg['GRAPH_VERSION'] + '/' + path
    params=dict(params or {})
    if cfg.get('APP_SECRET'):
        params['appsecret_proof']=hmac.new(cfg['APP_SECRET'].encode(),cfg['ACCESS_TOKEN'].encode(),hashlib.sha256).hexdigest()
    if params:
        url += '?' + urllib.parse.urlencode(params)
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=raw, headers={
        'Authorization': 'Bearer ' + cfg['ACCESS_TOKEN'], 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=20) as response:
            data = response.read(2 * 1024 * 1024)
        decoded=json.loads(data)
        if not isinstance(decoded,dict):raise ValueError('Resposta inválida')
        return decoded
    except urllib.error.HTTPError as exc:
        code = str(exc.code)
        try:
            error = json.loads(exc.read(65536)).get('error', {})
            if isinstance(error.get('code'), int):
                code = str(error['code'])
        except (ValueError, AttributeError):
            pass
        raise MetaError('A Meta recusou a solicitação. Código: ' + code, code, uncertain=payload is not None and (exc.code>=500 or exc.code==408)) from None
    except (OSError, ValueError, urllib.error.URLError):
        raise MetaError('Não foi possível confirmar a resposta da Meta. Não repita o envio antes de conferir o status.', uncertain=payload is not None) from None


def templates(cfg):
    result = []
    after = None
    for _ in range(20):
        params = {'fields': 'id,name,status,language,components', 'limit': '100'}
        if after:
            params['after'] = after
        page = graph(cfg, cfg['WABA_ID'] + '/message_templates', params=params)
        for row in page.get('data', []):
            if row.get('status') != 'APPROVED':
                continue
            components = []
            supported = True
            for c in row.get('components', []):
                typ = c.get('type', '').upper()
                if typ in ('HEADER', 'BODY'):
                    if typ == 'HEADER' and c.get('format', 'TEXT') != 'TEXT':
                        supported = False
                        break
                    text = c.get('text', '')
                    variables = re.findall(r'\{\{\s*([^{}]+?)\s*\}\}', text)
                    if any(not x.isdecimal() for x in variables):
                        supported = False
                        break
                    numbers = sorted(set(int(x) for x in variables))
                    if numbers and numbers != list(range(1, max(numbers) + 1)):
                        supported = False
                        break
                    components.append(dict(type=typ.lower(), text=text, count=len(numbers)))
                elif typ == 'FOOTER':
                    components.append(dict(type='footer', text=c.get('text',''), count=0))
                elif typ == 'BUTTONS':
                    # Dynamic URLs, OTP and quick-reply payloads require their own parameter editor.
                    supported = False
            if supported:
                result.append(dict(name=row['name'], language=row['language'], components=components))
        paging = page.get('paging', {})
        if not paging.get('next'):
            return result
        after = paging.get('cursors', {}).get('after')
        if not isinstance(after, str) or len(after) > 2048:
            raise MetaError('Paginação de modelos inválida na resposta da Meta.')
    raise MetaError('Muitos modelos para esta consulta. Refine a configuração da conta.')


def template_payload(template, supplied, number, request_id):
    if not isinstance(supplied, dict) or set(supplied) - {'header', 'body'}:
        raise ValueError('Parâmetros do modelo inválidos')
    components = []
    for c in template['components']:
        values = supplied.get(c['type'], [])
        if not isinstance(values, list) or len(values) != c['count'] or any(not isinstance(x, str) or not x.strip() or len(x) > 1024 for x in values):
            raise ValueError('Preencha todos os parâmetros do modelo aprovado')
        if values:
            components.append(dict(type=c['type'], parameters=[dict(type='text', text=x.strip()) for x in values]))
    if any(supplied.get(k) and k not in {c['type'] for c in template['components']} for k in supplied):
        raise ValueError('Parâmetro sem componente correspondente')
    return dict(messaging_product='whatsapp', to=number, type='template',
                biz_opaque_callback_data=request_id,
                template=dict(name=template['name'], language={'code': template['language']}, components=components))


def valid_signature(secret, body, signature):
    if not secret or not isinstance(signature, str):
        return False
    expected = 'sha256=' + hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
