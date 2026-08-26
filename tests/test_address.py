from __future__ import annotations

import unittest

from app.address import AddressLookupError, parse_viacep_payload


class AddressTest(unittest.TestCase):
    def test_formats_address_without_house_number(self) -> None:
        address = parse_viacep_payload(
            {
                "logradouro": "Rua Vandervaldo Rosa",
                "bairro": "Loteamento Monte Hebron",
                "localidade": "Uberlândia",
                "uf": "MG",
            },
            "38408-402",
        )

        self.assertEqual(address.formatted_postal_code, "38408-402")
        self.assertEqual(
            address.formatted,
            "Rua Vandervaldo Rosa, Loteamento Monte Hebron, Uberlândia/MG — "
            "CEP 38408-402",
        )

    def test_rejects_unknown_postal_code(self) -> None:
        with self.assertRaises(AddressLookupError):
            parse_viacep_payload({"erro": True}, "12345678")


if __name__ == "__main__":
    unittest.main()
