import unittest
from unittest.mock import Mock, patch
import support
from shelly.discovery import Discovery


class DiscoveryTests(unittest.TestCase):
    def test_add_and_changed_address_use_service_port(self):
        found = Mock()
        with patch('zeroconf.Zeroconf') as factory, patch('zeroconf.ServiceBrowser'):
            discovery = Discovery(found)
            zc = factory.return_value
            info = zc.get_service_info.return_value
            info.port = 8080
            info.parsed_addresses.return_value = ['192.0.2.1']
            discovery.add_service(zc, '_shelly._tcp.local.', 'unit')
            found.assert_called_with('192.0.2.1:8080')
            info.parsed_addresses.return_value = ['192.0.2.2']
            discovery.update_service(zc, '_shelly._tcp.local.', 'unit')
            found.assert_called_with('192.0.2.2:8080')
            discovery.close()
            zc.close.assert_called_once()

    def test_unresolved_service_does_not_create_connection(self):
        found = Mock()
        with patch('zeroconf.Zeroconf') as factory, patch('zeroconf.ServiceBrowser'):
            discovery = Discovery(found)
            zc = factory.return_value
            zc.get_service_info.return_value = None
            discovery.add_service(zc, '_shelly._tcp.local.', 'unit')
            found.assert_not_called()
            discovery.close()
