import logging
import requests
from requests.adapters import HTTPAdapter
from config import TELEGRAM_BOT_TOKEN, STORE_NAME, DEFAULT_SUPPORT_USERNAME

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = 'https://api.telegram.org/bot'

_session = requests.Session()
_session.mount('https://', HTTPAdapter(pool_connections=20, pool_maxsize=50))

def send_telegram_message(chat_id, text, parse_mode='HTML', reply_markup=None):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith('YOUR_'):
        logger.warning(f'Telegram Bot Token not configured. Simulated sending to {chat_id}: {text[:50]}...')
        return {'ok': True, 'simulated': True}

    url = f'{TELEGRAM_API_URL}{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup

    try:
        resp = _session.post(url, json=payload, timeout=5)
        data = resp.json()
        if not data.get('ok'):
            logger.error(f'Telegram API error sending to {chat_id}: {data}')
        return data
    except Exception as e:
        logger.exception(f'Failed to send Telegram message to {chat_id}: {e}')
        return {'ok': False, 'error': str(e)}

def send_telegram_document(chat_id, file_path, caption=None):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith('YOUR_'):
        logger.warning(f'Telegram Bot Token not configured. Simulated sending document to {chat_id}')
        return {'ok': True, 'simulated': True}

    url = f'{TELEGRAM_API_URL}{TELEGRAM_BOT_TOKEN}/sendDocument'
    try:
        with open(file_path, 'rb') as f:
            files = {'document': f}
            data = {'chat_id': chat_id}
            if caption:
                data['caption'] = caption
                data['parse_mode'] = 'HTML'
            resp = _session.post(url, data=data, files=files, timeout=8)
            return resp.json()
    except Exception as e:
        logger.exception(f'Failed to send Telegram document to {chat_id}: {e}')
        return {'ok': False, 'error': str(e)}
