# coding: utf-8
import json
import logging
import requests.adapters
from collections import OrderedDict
from requests.exceptions import RequestException

from . import conf, utils
from .enums import EndpointUrl
from .exceptions import CsobBaseException


def _get_session():
    from requests import Session
    return Session()

try:
    from django.conf import settings
    session_factory = getattr(settings, "PYCSOB_REQUESTS_SESSION_FACTORY", _get_session)
except ImportError:
    session_factory = _get_session


log = logging.getLogger('pycsob')


class HTTPAdapter(requests.adapters.HTTPAdapter):
    """
    HTTP adapter with default timeout
    """

    def send(self, request, **kwargs):
        kwargs.setdefault('timeout', conf.HTTP_TIMEOUT)
        try:
            return super(HTTPAdapter, self).send(request, **kwargs)
        except RequestException as raised_exception:
            raise CsobBaseException(raised_exception) from raised_exception


class CsobClient(object):
    def __init__(self, merchant_id, base_url, private_key, csob_pub_key):
        """
        Initialize Client

        :param merchant_id: Your Merchant ID (you can find it in POSMerchant)
        :param base_url: Base API url development / production
        :param private_key: CSOB private key string
        :param csob_pub_key: Path to CSOB public key file, or its contents
        """
        self.merchant_id = merchant_id
        self.base_url = base_url
        self.key = private_key
        self.pubkey = self._get_key(csob_pub_key)

        session = session_factory()
        session.headers = conf.HEADERS
        session.mount('https://', HTTPAdapter())
        session.mount('http://', HTTPAdapter())

        self._client = session

    def _get_key(self, value):
        try:
            with open(value) as opened_file:
                return opened_file.read()
        except FileNotFoundError:
            return value

    def payment_init(self, order_no, total_amount, return_url, description, customer_data,
                     merchant_data=None, cart=None, customer_id=None, currency='CZK', language='CZ',
                     close_payment=True, return_method='POST', pay_operation='payment', ttl_sec=600,
                     logo_version=None, color_scheme_version=None):
        """
        Initialize transaction, sum of cart items must be equal to total amount
        If cart is None, we create it for you from total_amount and description values.

        The payload structure must follow the signature structure order. Please follow the documentation
        https://github.com/csob/platebnibrana/wiki/Podpis-po%C5%BEadavku-a-ov%C4%9B%C5%99en%C3%AD-podpisu-odpov%C4%9Bdi

        Cart example::

            cart = [
                OrderedDict([
                    ('name', 'Order in sho XYZ'),
                    ('quantity', 5),
                    ('amount', 12345),
                ]),
                OrderedDict([
                    ('name', 'Postage'),
                    ('quantity', 1),
                    ('amount', 0),
                ])
            ]

        :param order_no: order number
        :param total_amount:
        :param return_url: URL to be returned to from payment gateway
        :param cart: items in cart, currently min one item, max two as mentioned in CSOB spec
        :param description: order description
        :param customer_data: dict with customer name and either email or phone
        :param customer_id: optional customer id
        :param language: supported languages: 'CZ', 'EN', 'DE', 'SK', 'HU', 'IT', 'JP', 'PL', 'PT', 'RO', 'RU', 'SK', 'ES', 'TR' or 'VN'
        :param currency: supported currencies: 'CZK', 'EUR', 'USD', 'GBP'
        :param close_payment:
        :param return_method: method which be used for return to shop from gateway POST (default) or GET
        :param pay_operation: `payment` or `oneclickPayment`
        :return: response from gateway as OrderedDict
        """

        if len(description) > 20:
            raise ValueError('Description length is over 20 chars')

        # fill cart if not set
        if not cart:
            cart = [
                OrderedDict([
                    ('name', description),
                    ('quantity', 1),
                    ('amount', total_amount)
                ])
            ]

        payload = utils.mk_payload(self.key, pairs=(
            ('merchantId', self.merchant_id),
            ('orderNo', str(order_no)),
            ('dttm', utils.dttm()),
            ('payOperation', pay_operation),
            ('payMethod', 'card'),
            ('totalAmount', total_amount),
            ('currency', currency),
            ('closePayment', close_payment),
            ('returnUrl', return_url),
            ('returnMethod', return_method),
            ('cart', cart),
            ('customer', utils.convert_keys_to_camel_case(customer_data)),
            ('merchantData', merchant_data),
            ('customerId', customer_id),
            ('language', language),
            ('ttlSec', ttl_sec),
            ('logoVersion', logo_version),
            ('colorSchemeVersion', color_scheme_version),
        ))
        url = utils.mk_url(base_url=self.base_url, endpoint_url=EndpointUrl.PAYMENT_INIT)
        r = self._client.post(url, data=json.dumps(payload))
        return utils.validate_response(r, self.pubkey)

    def get_payment_process_url(self, pay_id):
        """
        :param pay_id: pay_id obtained from payment_init()
        :return: url to process payment
        """
        return utils.mk_url(
            base_url=self.base_url,
            endpoint_url=EndpointUrl.PAYMENT_PROCESS,
            payload=self.req_payload(pay_id=pay_id)
        )

    def gateway_return(self, datadict):
        """
        Return from gateway as OrderedDict

        :param datadict: data from request in dict
        :return: verified data or raise error
        """
        o = OrderedDict()
        for k in conf.RESPONSE_KEYS:
            if k in datadict:
                o[k] = int(datadict[k]) if k in ('resultCode', 'paymentStatus') else datadict[k]
        if not utils.verify(o, datadict['signature'], self.pubkey):
            raise utils.CsobVerifyError('Unverified gateway return data')
        return o

    def payment_status(self, pay_id):
        url = utils.mk_url(
            base_url=self.base_url,
            endpoint_url=EndpointUrl.PAYMENT_STATUS,
            payload=self.req_payload(pay_id=pay_id)
        )
        r = self._client.get(url=url)
        return utils.validate_response(r, self.pubkey)

    def payment_reverse(self, pay_id):
        url = utils.mk_url(
            base_url=self.base_url,
            endpoint_url=EndpointUrl.PAYMENT_REVERSE,
        )
        payload = self.req_payload(pay_id)
        r = self._client.put(url, data=json.dumps(payload))
        return utils.validate_response(r, self.pubkey)

    def payment_close(self, pay_id, total_amount=None):
        url = utils.mk_url(
            base_url=self.base_url,
            endpoint_url=EndpointUrl.PAYMENT_CLOSE,
        )
        payload = self.req_payload(pay_id, totalAmount=total_amount)
        r = self._client.put(url, data=json.dumps(payload))
        return utils.validate_response(r, self.pubkey)

    def payment_refund(self, pay_id, amount=None):
        url = utils.mk_url(
            base_url=self.base_url,
            endpoint_url=EndpointUrl.PAYMENT_REFUND,
        )

        payload = self.req_payload(pay_id, amount=amount)
        r = self._client.put(url, data=json.dumps(payload))
        return utils.validate_response(r, self.pubkey)

    def customer_info(self, customer_id):
        """
        :param customer_id: e-shop customer ID
        :return: data from JSON response or raise error
        """
        url = utils.mk_url(
            base_url=self.base_url,
            endpoint_url=EndpointUrl.CUSTOMER_INFO,
            payload=utils.mk_payload(self.key, pairs=(
                ('merchantId', self.merchant_id),
                ('customerId', customer_id),
                ('dttm', utils.dttm())
            ))
        )
        r = self._client.get(url)
        return utils.validate_response(r, self.pubkey)

    def oneclick_init(self, orig_pay_id, order_no, total_amount, customer_data, currency='CZK', description=None,
                      return_url='http://localhost', return_method='GET', client_initiated=False):
        """
        Initialize one-click payment. Before this, you need to call payment_init(..., pay_operation='oneclickPayment')
        It will create payment template for you. Use pay_id returned from payment_init as orig_pay_id in this method.

        The payload structure must follow the signature structure order. Please follow the documentation
        https://github.com/csob/platebnibrana/wiki/Podpis-po%C5%BEadavku-a-ov%C4%9B%C5%99en%C3%AD-podpisu-odpov%C4%9Bdi
        """

        payload = utils.mk_payload(self.key, pairs=(
            ('merchantId', self.merchant_id),
            ('origPayId', orig_pay_id),
            ('orderNo', str(order_no)),
            ('dttm', utils.dttm()),
            ('totalAmount', total_amount),
            ('currency', currency),
            ('description', description),
            ('returnUrl', return_url),
            ('returnMethod', return_method),
            ('customer', utils.convert_keys_to_camel_case(customer_data)),
            ('clientInitiated', client_initiated),
        ))
        url = utils.mk_url(base_url=self.base_url, endpoint_url=EndpointUrl.ONE_CLICK_INIT)
        r = self._client.post(url, data=json.dumps(payload))
        return utils.validate_response(r, self.pubkey)

    def oneclick_start(self, pay_id):
        """
        Start one-click payment. After 2 - 3 seconds it is recommended to call payment_status().

        :param pay_id: use pay_id returned by oneclick_init()
        """

        payload = utils.mk_payload(self.key, pairs=(
            ('merchantId', self.merchant_id),
            ('payId', pay_id),
            ('dttm', utils.dttm()),
        ))
        url = utils.mk_url(base_url=self.base_url, endpoint_url=EndpointUrl.ONE_CLICK_PROCESS)
        r = self._client.post(url, data=json.dumps(payload))
        return utils.validate_response(r, self.pubkey)

    def echo(self, method='POST'):
        """
        Echo call for development purposes/gateway tests

        :param method: request method (GET/POST), default is POST
        :return: data from JSON response or raise error
        """
        payload = utils.mk_payload(self.key, pairs=(
            ('merchantId', self.merchant_id),
            ('dttm', utils.dttm())
        ))
        if method.lower() == 'post':
            url = utils.mk_url(
                base_url=self.base_url,
                endpoint_url=EndpointUrl.ECHO,
            )
            r = self._client.post(url, data=json.dumps(payload))
        else:
            url = utils.mk_url(
                base_url=self.base_url,
                endpoint_url='echo/',
                payload=payload
            )
            r = self._client.get(url)

        return utils.validate_response(r, self.pubkey)

    def req_payload(self, pay_id, **kwargs):
        pairs = (
            ('merchantId', self.merchant_id),
            ('payId', pay_id),
            ('dttm', utils.dttm()),
        )
        for k, v in kwargs.items():
            if v not in conf.EMPTY_VALUES:
                pairs += ((k, v),)
        return utils.mk_payload(key=self.key, pairs=pairs)
