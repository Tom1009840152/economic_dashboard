import unittest
from datetime import date
from types import SimpleNamespace

from fastapi import HTTPException

from app.routers.forecast import get_forecast
from app.routers.indicators import _freshness


class _IndicatorOnlySession:
    def get(self, _model, code: str):
        return SimpleNamespace(code=code, name="韩国银行基准利率")


class EventPolicyRateRouteTests(unittest.TestCase):
    def test_korean_base_rate_is_labelled_event_driven(self) -> None:
        point = SimpleNamespace(date=date(2008, 8, 7))

        self.assertEqual(_freshness([point], "KR_BOK"), ("event", "按决议更新"))

    def test_korean_base_rate_is_not_mechanically_forecast(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            get_forecast("KR_BOK", horizon=None, db=_IndicatorOnlySession())

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("event-driven", caught.exception.detail)

    def test_portwatch_rolling_pulse_is_not_mechanically_forecast(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            get_forecast(
                "PW_WLD_CNTR_SHIP_30D_YOY",
                horizon=None,
                db=_IndicatorOnlySession(),
            )

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("observation signals", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
