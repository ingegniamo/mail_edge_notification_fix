

import textwrap
import logging as logger

from urllib.parse import urlsplit
from odoo.addons.mail.web_push import _iv, _derive_key, _encrypt_payload,  DeviceUnreachableError
from odoo.addons.mail.tools import jwt

_logger = logger.getLogger(__name__)
def push_to_end_point(base_url, device, payload, vapid_private_key, vapid_public_key, session):
    """
    https://www.rfc-editor.org/rfc/rfc8291
    """
    endpoint = device["endpoint"]
    url = urlsplit(endpoint)
    jwt_claims = {
        # aud: The “Audience” is a JWT construct that indicates the recipient scheme and host
        # e.g. for an endpoint like https://updates.push.services.mozilla.com/wpush/v2/gAAAAABY...,
        #      the “aud” would be https://updates.push.services.mozilla.com
        'aud': '{}://{}'.format(url.scheme, url.netloc),
        # sub: the sub value needs to be either a URL address. This is so that if a push service needed to reach out
        # to sender, it can find contact information from the JWT.
        'sub': base_url,
    }
    token = jwt.sign(jwt_claims, vapid_private_key, ttl=12 * 60 * 60, algorithm=jwt.Algorithm.ES256)
    body_payload = payload.encode()
    payload = _encrypt_payload(body_payload, device)
    headers = {
        #  Authorization header field contains these parameters:
        #  - "t" is the JWT;
        #  - "k" the base64url-encoded key that signed that token.
        'Authorization': 'vapid t={}, k={}'.format(token, vapid_public_key),
        'Content-Encoding': 'aes128gcm',
        # The TTL is set to '60' as workaround because the push notifications 
        # are not received on Edge with TTL ='0'. 
        # Using the TTL '0' , the microsoft endpoint returns a 400 bad request error.
        # and we are sure that the notification will be received
        'TTL': '60',
    }

    response = session.post(endpoint, headers=headers, data=payload, timeout=5)
    if response.status_code == 201:
        _logger.debug('Sent push notification %s', endpoint)
    else:
        error_message_shorten = textwrap.shorten(response.text, 100)
        _logger.warning('Failed push notification %s %d - %s',
                        endpoint, response.status_code, error_message_shorten)

        # Invalid subscription
        if response.status_code == 404 or response.status_code == 410:
            raise DeviceUnreachableError("Device Unreachable")
        

