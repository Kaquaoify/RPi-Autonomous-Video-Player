import logging
try:
    from flask import current_app
    _svc_logger = current_app.logger
except Exception:
    _svc_logger = logging.getLogger('rpi_avp')

