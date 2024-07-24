import logging
import sys
import re
from typing import Any, TypeVar
from base64 import b64encode, b64decode
from collections import OrderedDict
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5
from json import JSONDecodeError
from urllib.parse import urljoin, quote_plus

from . import conf
from .exceptions import CsobBaseException, CsobJSONDecodeError, CsobVerifyError
from requests.exceptions import HTTPError


logger = logging.getLogger(__name__)


try:
    from django.utils import timezone as datetime
except ImportError:
    from datetime import datetime


def sign(payload, key):
    msg = mk_msg_for_sign(payload)
    h = SHA256.new(msg)
    signer = PKCS1_v1_5.new(RSA.importKey(key))
    return b64encode(signer.sign(h)).decode()


def verify(payload, signature, pubkey):
    msg = mk_msg_for_sign(payload)
    key = RSA.importKey(pubkey)
    h = SHA256.new(msg)
    verifier = PKCS1_v1_5.new(key)
    return verifier.verify(h, b64decode(signature))


def mk_msg_for_sign(payload):
    payload = {k: v for k, v in payload.items() if v is not None}
    if 'cart' in payload and payload['cart'] not in conf.EMPTY_VALUES:
        cart_msg = []
        for one in payload['cart']:
            cart_msg.extend(one.values())
        payload['cart'] = '|'.join(map(str_or_jsbool, cart_msg))
    if payload.get('customer') not in conf.EMPTY_VALUES:
        payload['customer'] = get_customer_data_signature_message(payload['customer'])
    msg = '|'.join(map(str_or_jsbool, payload.values()))
    return msg.encode('utf-8')


def mk_payload(key, pairs):
    payload = OrderedDict([(k, v) for k, v in pairs if v not in conf.EMPTY_VALUES])
    payload['signature'] = sign(payload, key)
    return payload


def mk_url(base_url, endpoint_url, payload=None):
    url = urljoin(base_url, endpoint_url)
    if payload is None:
        return url
    return urljoin(url, '/'.join(map(quote_plus, payload.values())))


def str_or_jsbool(v):
    if type(v) == bool:
        return str(v).lower()
    return str(v)


def dttm(format_='%Y%m%d%H%M%S'):
    return datetime.now().strftime(format_)


def validate_response(response, key):
    try:
        response.raise_for_status()
        data = response.json()
    except JSONDecodeError:
        raise CsobJSONDecodeError('Cannot decode JSON in response')
    except HTTPError as raised_exception:
        raise CsobBaseException(raised_exception)

    signature = data.pop('signature')
    payload = OrderedDict()

    for k in conf.RESPONSE_KEYS:
        if k in data:
            payload[k] = data[k]

    if not verify(payload, signature, key):
        raise CsobVerifyError('Cannot verify response')

    response.extensions = []
    response.payload = payload

    # extensions
    if 'extensions' in data:
        maskclnrp_keys = 'extension', 'dttm', 'maskedCln', 'expiration', 'longMaskedCln'
        for one in data['extensions']:
            if one['extension'] in ('maskClnRP', 'maskCln'):
                o = OrderedDict()
                for k in maskclnrp_keys:
                    if k in one:
                        o[k] = one[k]
                if verify(o, one['signature'], key):
                    response.extensions.append(o)
                else:
                    raise CsobVerifyError('Cannot verify masked card extension response')

    return response


PROVIDERS = (
    (conf.CARD_PROVIDER_VISA, re.compile(r'^4\d{5}$')),
    (conf.CARD_PROVIDER_AMEX, re.compile(r'^3[47]\d{4}$')),
    (conf.CARD_PROVIDER_DINERS, re.compile(r'^3(?:0[0-5]|[68][0-9])[0-9]{4}$')),
    (conf.CARD_PROVIDER_JCB, re.compile(r'^(?:2131|1800|35[0-9]{2})[0-9]{2}$')),
    (conf.CARD_PROVIDER_MC, re.compile(r'^5[1-5][0-9]{4}|222[1-9][0-9]{2}|22[3-9][0-9]{4}|2[3-6][0-9]{5}|27[01][0-9]{4}|2720[0-9]{2}$')),
)


def get_card_provider(long_masked_number):
    for provider_id, rx in PROVIDERS:
        if rx.match(long_masked_number[:6]):
            return provider_id, conf.CARD_PROVIDERS[provider_id]
    return None, None


def to_camel_case(value: str) -> str:
    """
    Convert the value from snake_case to camelCase format. If the value is not in the snake_case format, return
    the original value.
    """
    first_word, *other_words = value.split('_')
    return ''.join([first_word.lower(), *map(str.title, other_words)]) if other_words else first_word


T = TypeVar('T', list[Any], dict[str, Any])


def convert_keys_to_camel_case(data: T) -> T:
    """
    Convert all dictionary keys and nested dictionary keys from snake_case to camelCase format.
    Returns the same data type that was in the input of the function in the data parameter.
    """
    if not data:
        return data

    if isinstance(data, list):
        return [convert_keys_to_camel_case(value) if isinstance(value, (dict, list)) else value for value in data]

    converted_dict = {}
    for key, value in data.items():
        if isinstance(key, str):
            key = to_camel_case(key)
        else:
            logger.error(
                "Incorrect value type '%s' during conversion to camcel case. String expected.", type(key)
            )

        if isinstance(value, (dict, list)):
            converted_dict[key] = convert_keys_to_camel_case(value)
        else:
            converted_dict[key] = value
    return converted_dict


def get_customer_data_signature_message(customer_data: dict[str, Any]) -> str:
    """
    Returns signature string from customer data used to sign the request.
    For more information follow the API documentation
    https://github.com/csob/platebnibrana/wiki/Podpis-po%C5%BEadavku-a-ov%C4%9B%C5%99en%C3%AD-podpisu-odpov%C4%9Bdi
    """

    def get_joined_values(data: dict[str, Any], keys: list) -> str:
        """
        Args:
            data: payload customer data
            keys: list with ordered keys
        """
        return '|'.join(str_or_jsbool(data[key]) for key in keys if key in data)

    customer_keys = ['name', 'email', 'mobilePhone']
    account_keys = ['createdAt', 'changedAt']
    login_keys = ['auth', 'authAt']

    customer_msg = get_joined_values(customer_data, customer_keys)
    account_msg = get_joined_values(customer_data.get('account', {}), account_keys)
    login_msg = get_joined_values(customer_data.get('login', {}), login_keys)

    return '|'.join(filter(None, (customer_msg, account_msg, login_msg)))
