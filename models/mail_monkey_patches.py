

import logging as logger
from odoo.addons.mail.web_push import _iv, _derive_key, _encrypt_payload,  DeviceUnreachableError
from requests import Session
import json
from ..web_push import push_to_end_point
from odoo.addons.mail.models.mail_thread import MailThread
from odoo.addons.mail.models.web_push import WebPush
MAX_DIRECT_PUSH = 5
_logger = logger.getLogger(__name__)

def _notify_thread_by_web_push(self, message, recipients_data, msg_vals=False, **kwargs):
    """ Method to send cloud notifications for every mention of a partner
    and every direct message. We have to take into account the risk of
    duplicated notifications in case of a mention in a channel of `chat` type.

    :param message: ``mail.message`` record to notify;
    :param recipients_data: list of recipients information (based on res.partner
        records), formatted like
        [{'active': partner.active;
            'id': id of the res.partner being recipient to notify;
            'groups': res.group IDs if linked to a user;
            'notif': 'inbox', 'email', 'sms' (SMS App);
            'share': partner.partner_share;
            'type': 'customer', 'portal', 'user;'
            }, {...}].
        See ``MailThread._notify_get_recipients``;
    :param msg_vals: dictionary of values used to create the message. If given it
        may be used to access values related to ``message`` without accessing it
        directly. It lessens query count in some optimized use cases by avoiding
        access message content in db;
    """

    msg_vals = dict(msg_vals or {})
    partner_ids = self._extract_partner_ids_for_notifications(message, msg_vals, recipients_data)
    if not partner_ids:
        return

    partner_devices_sudo = self.env['mail.partner.device'].sudo()
    devices = partner_devices_sudo.search([
        ('partner_id', 'in', partner_ids)
    ])
    if not devices:
        return

    ir_parameter_sudo = self.env['ir.config_parameter'].sudo()
    vapid_private_key = ir_parameter_sudo.get_param('mail.web_push_vapid_private_key')
    vapid_public_key = ir_parameter_sudo.get_param('mail.web_push_vapid_public_key')
    if not vapid_private_key or not vapid_public_key:
        _logger.warning("Missing web push vapid keys !")
        return

    payload = self._notify_by_web_push_prepare_payload(message, msg_vals=msg_vals)
    payload = self._truncate_payload(payload)
    if len(devices) < MAX_DIRECT_PUSH:
        session = Session()
        devices_to_unlink = set()
        for device in devices:
            try:
                push_to_end_point(
                    base_url=self.get_base_url(),
                    device={
                        'id': device.id,
                        'endpoint': device.endpoint,
                        'keys': device.keys
                    },
                    payload=json.dumps(payload),
                    vapid_private_key=vapid_private_key,
                    vapid_public_key=vapid_public_key,
                    session=session,
                )
            except DeviceUnreachableError:
                devices_to_unlink.add(device.id)
            except Exception as e:  # pylint: disable=broad-except
                # Avoid blocking the whole request just for a notification
                _logger.error('An error occurred while contacting the endpoint: %s', e)

        # clean up obsolete devices
        if devices_to_unlink:
            devices_list = list(devices_to_unlink)
            self.env['mail.partner.device'].sudo().browse(devices_list).unlink()

    else:
        self.env['mail.notification.web.push'].sudo().create([{
            'user_device': device.id,
            'payload': json.dumps(payload),
        } for device in devices])
        self.env.ref('mail.ir_cron_web_push_notification')._trigger()

def _push_notification_to_endpoint(self, batch_size=50):
    """Send to web browser endpoint computed notification"""
    web_push_notifications_sudo = self.sudo().search_fetch([], ['user_device', 'payload'], limit=batch_size)
    if not web_push_notifications_sudo:
        return

    ir_parameter_sudo = self.env['ir.config_parameter'].sudo()
    vapid_private_key = ir_parameter_sudo.get_param('mail.web_push_vapid_private_key')
    vapid_public_key = ir_parameter_sudo.get_param('mail.web_push_vapid_public_key')
    if not vapid_private_key or not vapid_public_key:
        return

    session = Session()
    devices_to_unlink = set()

    # process send notif
    devices = web_push_notifications_sudo.user_device.grouped('id')
    for web_push_notification_sudo in web_push_notifications_sudo:
        device = devices.get(web_push_notification_sudo.user_device.id)
        if device.id in devices_to_unlink:
            continue
        try:
            push_to_end_point(
                base_url=self.get_base_url(),
                device={
                    'id': device.id,
                    'endpoint': device.endpoint,
                    'keys': device.keys
                },
                payload=web_push_notification_sudo.payload,
                vapid_private_key=vapid_private_key,
                vapid_public_key=vapid_public_key,
                session=session,
            )
        except DeviceUnreachableError:
            devices_to_unlink.add(device.id)

    # clean up notif
    web_push_notifications_sudo.unlink()

    # clean up obsolete devices
    if devices_to_unlink:
        self.env['mail.partner.device'].sudo().browse(devices_to_unlink).unlink()

    # restart the cron if needed
    if self.search_count([]) > 0:
        self.env.ref('mail.ir_cron_web_push_notification')._trigger()


WebPush._push_notification_to_endpoint = _push_notification_to_endpoint
MailThread._notify_thread_by_web_push = _notify_thread_by_web_push