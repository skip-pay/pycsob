# coding: utf-8
import json
import os
import pytest
import responses
from collections import OrderedDict
from datetime import datetime
from freezegun import freeze_time
from unittest import TestCase
from requests.exceptions import HTTPError, ConnectionError
from pycsob.utils import convert_keys_to_camel_case, to_camel_case, get_customer_data_signature_message, mk_msg_for_sign

from pycsob import conf, utils
from pycsob.client import CsobClient
from pycsob.exceptions import CsobBaseException, CsobJSONDecodeError


BASE_URL = 'https://localhost'
KEY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), 'fixtures', 'test.key'))
PAY_ID = '34ae55eb69e2cBF'


class CsobClientTests(TestCase):

    def setUp(self):
        self.key = open(KEY_PATH).read()
        self.c = CsobClient(merchant_id='MERCHANT',
                            base_url=BASE_URL,
                            private_key=self.key,
                            csob_pub_key=KEY_PATH)

    def test_client_init_can_take_key_string(self):
        client = CsobClient(merchant_id='MERCHANT',
                            base_url=BASE_URL,
                            private_key=self.key,
                            csob_pub_key=self.key)
        assert client.key == self.key
        assert client.pubkey == self.key

    @freeze_time(datetime.now())
    @responses.activate
    def test_echo_post(self):
        resp_payload = utils.mk_payload(self.key, pairs=(
            ('dttm', utils.dttm()),
            ('resultCode', conf.RETURN_CODE_OK),
            ('resultMessage', 'OK'),
        ))
        responses.add(responses.POST, utils.mk_url(BASE_URL, '/echo/'), body=json.dumps(resp_payload),
                      status=200, content_type='application/json')
        out = self.c.echo().payload
        assert out['dttm'] == resp_payload['dttm']
        assert out['resultCode'] == conf.RETURN_CODE_OK

        sig = resp_payload.pop('signature')
        assert utils.verify(out, sig, self.key)

    @freeze_time(datetime.now())
    @responses.activate
    def test_echo_get(self):
        payload = utils.mk_payload(self.key, pairs=(
            ('merchantId', self.c.merchant_id),
            ('dttm', utils.dttm()),
        ))
        resp_payload = utils.mk_payload(self.key, pairs=(
            ('dttm', utils.dttm()),
            ('resultCode', conf.RETURN_CODE_OK),
            ('resultMessage', 'OK'),
        ))
        responses.add(responses.GET, utils.mk_url(BASE_URL, '/echo/', payload), body=json.dumps(resp_payload),
                      status=200, content_type='application/json')
        out = self.c.echo(method='GET').payload
        assert out['dttm'] == resp_payload['dttm']
        assert out['resultCode'] == conf.RETURN_CODE_OK

    def test_sign_message(self):
        msg = 'Příliš žluťoučký kůň úpěl ďábelské ódy.'
        payload = utils.mk_payload(self.key, pairs=(
            ('merchantId', self.c.merchant_id),
            ('dttm', utils.dttm()),
            ('description', msg)
        ))
        assert payload['description'] == msg
        sig = payload.pop('signature')
        assert utils.verify(payload, sig, self.key)

    @freeze_time(datetime.now())
    @responses.activate
    def test_payment_init_success(self):
        resp_payload = utils.mk_payload(self.key, pairs=(
            ('payId', PAY_ID),
            ('dttm', utils.dttm()),
            ('resultCode', conf.RETURN_CODE_OK),
            ('resultMessage', 'OK'),
            ('paymentStatus', 1),
        ))
        responses.add(responses.POST, utils.mk_url(BASE_URL, '/payment/init'), body=json.dumps(resp_payload),
                      status=200)
        response = self.c.payment_init(
            order_no=666,
            total_amount='66600',
            return_url='http://example.com',
            description='Nějaký popis',
            customer_data={
                'name': "Jiri Novak",
                'email': "j@novak.cz",
                'mobile_phone': "+420.602123123",
            },
        )

        request_body = json.loads(response.request.body)
        assert request_body['customer'] == {
            'name': "Jiri Novak",
            'email': "j@novak.cz",
            'mobilePhone': "+420.602123123",
        }
        payload = response.payload
        assert payload['paymentStatus'] == conf.PAYMENT_STATUS_INIT
        assert payload['resultCode'] == conf.RETURN_CODE_OK
        assert len(responses.calls) == 1

    @freeze_time(datetime.now())
    @responses.activate
    def test_onelick_init_success(self):
        resp_payload = utils.mk_payload(self.key, pairs=(
            ('payId', PAY_ID),
            ('dttm', utils.dttm()),
            ('resultCode', conf.RETURN_CODE_OK),
            ('resultMessage', 'OK'),
            ('paymentStatus', 1),
        ))
        responses.add(
            responses.POST, utils.mk_url(BASE_URL, '/oneclick/init'), body=json.dumps(resp_payload), status=200
        )
        response = self.c.oneclick_init(
            orig_pay_id=PAY_ID,
            order_no=666,
            total_amount='66600',
            return_url='http://example.com',
            customer_data={
                'name': "Jiri Novak",
                'email': "j@novak.cz",
                'mobile_phone': "+420.602123123",
            },
        )

        request_body = json.loads(response.request.body)
        assert request_body['customer'] == {
            'name': "Jiri Novak",
            'email': "j@novak.cz",
            'mobilePhone': "+420.602123123",
        }
        payload = response.payload
        assert payload['paymentStatus'] == conf.PAYMENT_STATUS_INIT
        assert payload['resultCode'] == conf.RETURN_CODE_OK
        assert len(responses.calls) == 1

    @freeze_time(datetime.now())
    @responses.activate
    def test_payment_init_bad_cart(self):
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
        resp_payload = utils.mk_payload(self.key, pairs=(
            ('payId', PAY_ID),
            ('dttm', utils.dttm()),
            ('resultCode', conf.RETURN_CODE_PARAM_INVALID),
            ('resultMessage', "Invalid 'cart' amounts, does not sum to totalAmount"),
            ('paymentStatus', conf.PAYMENT_STATUS_REJECTED),
        ))
        responses.add(responses.POST, utils.mk_url(BASE_URL, '/payment/init'), body=json.dumps(resp_payload),
                      status=200)
        out = self.c.payment_init(
            order_no=666,
            total_amount='2200000',
            return_url='http://',
            description='X',
            cart=cart,
            customer_data={
                'name': "Jiri Novak",
                'email': "j@novak.cz",
                'mobile_phone': "+420.602123123",
            },
        ).payload

        assert out['paymentStatus'] == conf.PAYMENT_STATUS_REJECTED
        assert out['resultCode'] == conf.RETURN_CODE_PARAM_INVALID

    @freeze_time(datetime.now())
    @responses.activate
    def test_payment_status_extension(self):

        payload = utils.mk_payload(self.key, pairs=(
            ('merchantId', self.c.merchant_id),
            ('payId', PAY_ID),
            ('dttm', utils.dttm()),
        ))

        resp_payload = utils.mk_payload(self.key, pairs=(
            ('payId', PAY_ID),
            ('dttm', utils.dttm()),
            ('resultCode', conf.RETURN_CODE_PARAM_INVALID),
            ('resultMessage', "OK"),
            ('paymentStatus', conf.PAYMENT_STATUS_WAITING),
            ('authCode', 'F7A23E')
        ))
        ext_payload_mask_cln_rp = utils.mk_payload(self.key, pairs=(
            ('extension', 'maskClnRP'),
            ('dttm', utils.dttm()),
            ('maskedCln', '****1234'),
            ('expiration', '12/20'),
            ('longMaskedCln', 'PPPPPP****XXXX')
        ))
        ext_payload_mask_cln = utils.mk_payload(self.key, pairs=(
            ('extension', 'maskCln'),
            ('dttm', utils.dttm()),
            ('maskedCln', '****1234'),
            ('expiration', '12/20'),
            ('longMaskedCln', 'PPPPPP****XXXX')
        ))
        resp_payload['extensions'] = [ext_payload_mask_cln_rp, ext_payload_mask_cln]
        responses.add(responses.GET, utils.mk_url(BASE_URL, '/payment/status/', payload), body=json.dumps(resp_payload),
                      status=200)
        out = self.c.payment_status(PAY_ID)

        assert hasattr(out, 'extensions')
        assert len(out.extensions) == 2
        assert out.extensions[0]['longMaskedCln'] == ext_payload_mask_cln['longMaskedCln']
        assert out.extensions[1]['longMaskedCln'] == ext_payload_mask_cln['longMaskedCln']

    @responses.activate
    def test_http_status_raised(self):
        responses.add(responses.POST, utils.mk_url(BASE_URL, '/echo/'), status=500)
        with pytest.raises(CsobBaseException) as excinfo:
            self.c.echo(method='POST')
        assert '500 Server Error' in str(excinfo.value)

    def test_gateway_return_retype(self):
        resp_payload = utils.mk_payload(self.key, pairs=(
            ('resultCode', str(conf.RETURN_CODE_PARAM_INVALID)),
            ('paymentStatus', str(conf.PAYMENT_STATUS_WAITING)),
            ('authCode', 'F7A23E')
        ))
        r = self.c.gateway_return(dict(resp_payload))
        assert type(r['paymentStatus']) == int
        assert type(r['resultCode']) == int

    def test_get_card_provider(self):
        fn = utils.get_card_provider

        assert fn('423451****111')[0] == conf.CARD_PROVIDER_VISA

    @responses.activate
    def test_response_not_containing_json_should_be_handled(self):
        responses.add(responses.POST, utils.mk_url(BASE_URL, '/echo/'), body='<html><p>This is not JSON</p></html>',
                      status=200, content_type='text/html')
        with pytest.raises(CsobJSONDecodeError) as excinfo:
            self.c.echo(method='POST')
        assert 'Cannot decode JSON in response' in str(excinfo.value)

    @responses.activate
    def test_connection_exceptions_should_be_caught_and_be_handled(self):
        responses.add(responses.POST, utils.mk_url(BASE_URL, '/echo/'), body=ConnectionError('Can\'t connect'),
                      status=200, content_type='text/html')
        with pytest.raises(CsobBaseException) as excinfo:
            self.c.echo(method='POST')
        assert 'Can\'t connect' in str(excinfo.value)


class CsobUtilsTests(TestCase):
    def test_to_camel_case_should_convert_string_to_camel_case(self):
        assert to_camel_case('') == ''
        assert to_camel_case('THIS_IS_SNAKE_CASE') == 'thisIsSnakeCase'
        assert to_camel_case('thisIsSnakeCase') == 'thisIsSnakeCase'

    def test_convert_keys_to_camel_case_should_convert_dict_keys_to_camel_case(self):
        customer_data = {
            'name': 'Petr Novak',
            'mobile_phone': '+420.735293123',
            'addressInfo': {
                'address_count': 2,
                'addresses': [
                    {
                        'street_address': 'Malkovskeho',
                        'types': [],
                    },
                    {
                        'street_address': 'Holesovice',
                        'types': ['billing'],
                    },
                ]
            },
        }

        assert convert_keys_to_camel_case(customer_data) == {
            'name': 'Petr Novak',
            'mobilePhone': '+420.735293123',
            'addressInfo': {
                'addressCount': 2,
                'addresses': [
                    {
                        'streetAddress': 'Malkovskeho',
                        'types': [],
                    },
                    {
                        'streetAddress': 'Holesovice',
                        'types': ['billing'],
                    },
                ]
            },
        }

    def test_convert_keys_to_camel_case_should_convert_dict_keys_to_camel_case_even_inside_a_list(self):
        list_data = [
            {
                'customer_name': 'test',
                1:1,
            }
        ]
        assert convert_keys_to_camel_case(list_data) == [{'customerName': 'test', 1:1}]

    def test_convert_keys_to_camel_case_should_not_convert_list_of_strings_only(self):
        list_data = ['customer_name', 1, None]
        assert convert_keys_to_camel_case(list_data) == ['customer_name', 1, None]

    def test_convert_keys_to_camel_case_should_log_error_for_not_string_keys(self):
        with self.assertLogs() as logs:
            assert convert_keys_to_camel_case({None: None}) == {None: None}
            assert logs.records[0].message == (
                "Incorrect value type '<class 'NoneType'>' during conversion to camcel case. String expected."
            )

            assert convert_keys_to_camel_case({1: 'test'}) == {1: 'test'}
            assert logs.records[1].message == (
                "Incorrect value type '<class 'int'>' during conversion to camcel case. String expected."
            )

    def test_get_customer_data_signature_message_should_return_correct_string_format(self):
        customer = {
            'name':'Jan Novák',
            'email':'jan.novak@example.com',
            'mobilePhone':'+420.800300300',
            'account': {
                'createdAt':'2022-01-12T12:10:37+01:00',
                'changedAt':'2022-01-15T15:10:12+01:00'
            },
            'login': {
                'auth':'account',
                'authAt':'2022-01-25T13:10:03+01:00'
            }
        }

        assert get_customer_data_signature_message(customer) == (
            'Jan Novák|jan.novak@example.com|+420.800300300'
            '|2022-01-12T12:10:37+01:00|2022-01-15T15:10:12+01:00'
            '|account|2022-01-25T13:10:03+01:00'
        )

    def test_get_customer_data_signature_message_should_be_able_to_skip_missing_values(self):
        # missing account
        customer = {
            'name':'Jan Novák',
            'email':'jan.novak@example.com',
            'mobilePhone':'+420.800300300',
            'login': {
                'auth':'account',
                'authAt':'2022-01-25T13:10:03+01:00'
            }
        }

        assert get_customer_data_signature_message(customer) == (
            'Jan Novák|jan.novak@example.com|+420.800300300|account|2022-01-25T13:10:03+01:00'
        )

        # missing login
        customer = {
            'name':'Jan Novák',
            'email':'jan.novak@example.com',
            'mobilePhone':'+420.800300300',
            'account': {
                'createdAt':'2022-01-12T12:10:37+01:00',
                'changedAt':'2022-01-15T15:10:12+01:00'
            },
        }
        assert get_customer_data_signature_message(customer) == (
            'Jan Novák|jan.novak@example.com|+420.800300300|2022-01-12T12:10:37+01:00|2022-01-15T15:10:12+01:00'
        )

        # missing customer email
        customer = {
            'name':'Jan Novák',
            'mobilePhone':'+420.800300300',
        }
        assert get_customer_data_signature_message(customer) == (
            'Jan Novák|+420.800300300'
        )

        # missing customer personal data
        customer = {
            'account': {
                'createdAt':'2022-01-12T12:10:37+01:00',
            }
        }
        assert get_customer_data_signature_message(customer) == (
            '2022-01-12T12:10:37+01:00'
        )

    def test_get_customer_data_signature_message_should_ignore_invalid_keys(self):
        customer = {
            'name':'Jan Novák',
            'email':'jan.novak@example.com',
            'mobilePhone':'+420.800300300',
            'nonsense': 'Test',
            'account': {
                'createdAt':'2022-01-12T12:10:37+01:00',
                'changedAt':'2022-01-15T15:10:12+01:00',
                'nonsense': 'Test',
            },
            'login': {
                'auth':'account',
                'authAt':'2022-01-25T13:10:03+01:00',
                'nonsense': 'Test',
            }
        }
        assert get_customer_data_signature_message(customer) == (
            'Jan Novák|jan.novak@example.com|+420.800300300'
            '|2022-01-12T12:10:37+01:00|2022-01-15T15:10:12+01:00'
            '|account|2022-01-25T13:10:03+01:00'
        )

    def test_get_customer_data_signature_message_should_keep_correct_value_order(self):
        customer = {
            'mobilePhone':'+420.800300300',
            'email':'jan.novak@example.com',
            'name':'Jan Novák',
            'login': {
                'authAt':'2022-01-25T13:10:03+01:00',
                'auth':'account',
            },
            'account': {
                'changedAt':'2022-01-15T15:10:12+01:00',
                'createdAt':'2022-01-12T12:10:37+01:00',
            },
        }

        assert get_customer_data_signature_message(customer) == (
            'Jan Novák|jan.novak@example.com|+420.800300300'
            '|2022-01-12T12:10:37+01:00|2022-01-15T15:10:12+01:00'
            '|account|2022-01-25T13:10:03+01:00'
        )

    def test_mk_msg_for_sign_should_return_correctly_ordered_message(self):
        payload = {
            'merchantId':'M1MIPS0000',
            'orderNo':'5547',
            'dttm':'20220125131559',
            'payOperation':'payment',
            'payMethod':'card',
            'totalAmount':123400,
            'currency':'CZK',
            'closePayment': True,
            'returnUrl':'https://shop.example.com/return',
            'returnMethod':'POST',
            'cart':[
                {
                    'name': 'Wireless headphones',
                    'quantity': 1,
                    'amount': 123400
                },
                {
                    'name': 'Shipping',
                    'quantity': 1,
                    'amount': 0,
                    'description': 'DPL'
                }
            ],
            'customer': {
                'name':'Jan Novák',
                'email':'jan.novak@example.com',
                'mobilePhone':'+420.800300300',
                'account': {
                    'createdAt':'2022-01-12T12:10:37+01:00',
                    'changedAt':'2022-01-15T15:10:12+01:00'
                },
                'login': {
                    'auth':'account',
                    'authAt':'2022-01-25T13:10:03+01:00'
                }
            },
        }
        expected_message = (
            'M1MIPS0000|5547|20220125131559|payment|card|123400|CZK|true|https://shop.example.com/return|POST'
            '|Wireless headphones|1|123400|Shipping|1|0|DPL'
            '|Jan Novák|jan.novak@example.com|+420.800300300'
            '|2022-01-12T12:10:37+01:00|2022-01-15T15:10:12+01:00'
            '|account|2022-01-25T13:10:03+01:00'
        ).encode()
        assert mk_msg_for_sign(payload) == expected_message
