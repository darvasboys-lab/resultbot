"""ResultBot Dhan read-only connector: entirely offline unit tests."""
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from resultbot import dhan_readonly as dh


class DhanReadOnlyTests(unittest.TestCase):
    def test_get_only_allowlist(self):
        for path in ("/profile","/ip/getIP","/orders","/trades","/orders/123456789"):
            self.assertEqual(dh.safe_endpoint(path),dh.BASE+path)
        for path in ("/orders/1/cancel","/orders/slicing","/forever/orders",
                     "/orders/11?x=1","/RenewToken","/trades/111"):
            with self.assertRaises(dh.DhanReadError):
                dh.safe_endpoint(path)

    def test_broker_fill_price_never_fabricated(self):
        self.assertEqual(dh.fill_info({"orderStatus":"PENDING","quantity":10,
                                      "filledQty":0,"averageTradedPrice":850})[-1],"N/A")
        self.assertEqual(dh.fill_info({"orderStatus":"TRADED","quantity":10,
                                      "filledQty":10,"averageTradedPrice":0})[-1],"N/A")
        self.assertEqual(dh.fill_info({"orderStatus":"TRADED","quantity":10,
                                      "filledQty":10,"averageTradedPrice":123.45})[-1],"₹123.45")
        self.assertEqual(dh.fill_info({"orderStatus":"PART_TRADED","quantity":10,
                                      "filledQty":3,"averageTradedPrice":123.45})[1],"3/10")
        self.assertEqual(dh.fill_info({"orderStatus":"TRADED","quantity":10,
                                      "filledQty":15,"averageTradedPrice":123.45})[-1],"N/A")

    def test_account_binding_fails_closed(self):
        with patch.object(dh,"get_json",return_value={"dhanClientId":"1111111111"}):
            with self.assertRaises(dh.DhanReadError):
                dh.verified_profile("2222222222","x"*32)
        with patch.object(dh,"get_json",return_value={"dhanClientId":"2222222222"}):
            self.assertEqual(dh.verified_profile("2222222222","x"*32)["dhanClientId"],"2222222222")
        with self.assertRaises(dh.DhanReadError):
            dh.verified_rows([{"dhanClientId":"1111111111"}],"2222222222")

    def test_credentials_file_permissions_and_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/".dhan_readonly.json"
            dh.store_credentials("1100008885","x"*50,path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertEqual(dh.load_credentials(path),("1100008885","x"*50))
            path.chmod(0o644)
            with self.assertRaises(dh.DhanReadError):
                dh.load_credentials(path)

    def test_no_symlink_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            good=root/"good.json"
            good.write_text("{}")
            link=root/"link.json"
            link.symlink_to(good)
            with self.assertRaises(dh.DhanReadError):
                dh.store_credentials("1100008885","x"*50,link)
            with self.assertRaises(dh.DhanReadError):
                dh.load_credentials(link)

    def test_request_is_get_and_token_in_header_not_url(self):
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): return None
            def read(self,n): return json.dumps({"dhanClientId":"1100008885"}).encode()
        recorded=[]
        def fake_open(req,timeout=12):
            recorded.append(req)
            return Response()
        with patch.object(dh,"urlopen",side_effect=fake_open):
            self.assertEqual(dh.get_json("/profile","z"*32)["dhanClientId"],"1100008885")
        self.assertEqual(len(recorded),1)
        self.assertEqual(recorded[0].get_method(),"GET")
        self.assertNotIn("z"*32,recorded[0].full_url)
        self.assertEqual(recorded[0].get_header("Access-token"),"z"*32)

    def test_builtin_self_test(self):
        dh.self_test()


if __name__=="__main__":
    unittest.main()
