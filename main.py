
from datetime import datetime
import itertools
import json
import logging
from pprint import pformat
import requests
import time

from opentelemetry import metrics
from opentelemetry.exporter.prometheus_remote_write import PrometheusRemoteWriteMetricsExporter
from opentelemetry.sdk.metrics import MeterProvider

LOGGER=logging.getLogger('main')

class WedgeMonitorApp(object):
    DESCRIPTIONS = {
        "DeservedTotalSalaryEquivalent": "Total salary equivalent (deserved)",
        "DeservedEsppSalaryEquivalent": "ESPP salary equivalent (deserved)",
        "DeservedRsuSalaryEquivalent": "RSU salary equivalent (deserved)",
        "EsppSalaryEquivalent": "ESPP salary equivalent",
        # "ExpectedDeservedTotalSalaryEquivalent": expectDeservedTotalSalaryEquiv,
        # "ExpectedDeservedEsppSalaryEquivalent": expectDeservedEsppSalaryEquiv,
        # "ExpectedDeservedRsuSalaryEquivalent": expectRsuSalaryEquiv,
        # "ExpectedTotalSalaryEquivalent": expectTotalSalaryEquiv,
        # "ExpectedEsppSalaryEquivalent": expectEsppSalaryEquiv,
        # "ExpectedRsuSalaryEquivalent": expectRsuSalaryEquiv,
        "RsuSalaryEquivalent": "RSU salary equivalent",
        "SalaryAndBonus": "Salary and bonus",
        "StockPrice": "Stock price",
        # "StockPriceTargetHigh": targetHigh,
        # "StockPriceTargetLow": targetLow,
        # "StockPriceTargetMean": targetMean,
        # "StockPriceTargetMedian": targetMedian,
        "TotalSalaryEquivalent": "Total salary equivalent",
        "UsdToGbp": "USD to GBP"
    }

    UNITS = {
        "DeservedTotalSalaryEquivalent": "£",
        "DeservedEsppSalaryEquivalent": "£",
        "DeservedRsuSalaryEquivalent": "£",
        "EsppSalaryEquivalent": "£",
        # "ExpectedDeservedTotalSalaryEquivalent": expectDeservedTotalSalaryEquiv,
        # "ExpectedDeservedEsppSalaryEquivalent": expectDeservedEsppSalaryEquiv,
        # "ExpectedDeservedRsuSalaryEquivalent": expectRsuSalaryEquiv,
        # "ExpectedTotalSalaryEquivalent": expectTotalSalaryEquiv,
        # "ExpectedEsppSalaryEquivalent": expectEsppSalaryEquiv,
        # "ExpectedRsuSalaryEquivalent": expectRsuSalaryEquiv,
        "RsuSalaryEquivalent": "£",
        "SalaryAndBonus": "£",
        "StockPrice": "$",
        # "StockPriceTargetHigh": targetHigh,
        # "StockPriceTargetLow": targetLow,
        # "StockPriceTargetMean": targetMean,
        # "StockPriceTargetMedian": targetMedian,
        "TotalSalaryEquivalent": "£",
        "UsdToGbp": "1"
    }

    def __init__(self):
        self.exporter = None
        self.lastMinute = 0
        self.lastSecond = 0
        self.valueRecorders = {}


    def loadConfig(self):
        configFilename = "config.json"
        with open(configFilename) as fp:
            self.config = json.load(fp)
        LOGGER.info("Loaded config %s", self.config)


    def startExporter(self):
        self.exporter = PrometheusRemoteWriteMetricsExporter(
            endpoint=self.config["logzUrl"],
            headers={
                "Authorization": "Bearer " + self.config["logzToken"],
            }
        )
        push_interval = self.config["logzPushInterval"]

        metrics.set_meter_provider(MeterProvider())
        self.meter = metrics.get_meter(__name__)
        metrics.get_meter_provider().start_pipeline(self.meter, self.exporter, push_interval)


    def doEsppCalc(self, esppBuyPriceUsd, salaryAndBonus, stockQuote, usdToCurrency):
        conf = self.config
        esppBuyPrice = min(esppBuyPriceUsd, stockQuote) * conf["esppDiscountFactor"]
        esppBuyAmountUsd = min(salaryAndBonus * conf["esppSalaryFactor"] / usdToCurrency, conf["esppLimitUsd"]) * conf["esppDiscountFactor"]
        esppShareCount = int(esppBuyAmountUsd / esppBuyPrice)
        esppProfitAmount = esppShareCount * (stockQuote - esppBuyPrice)
        esppSalaryEquivUsd = esppProfitAmount * conf["employersNicFactor"]
        esppSalaryEquiv = esppSalaryEquivUsd * usdToCurrency
        LOGGER.info("You purchase %d ESPP shares per year with $%.2f, with profit equivalent to %s%.2f as salary",
            esppShareCount, esppBuyAmountUsd, conf["currencySymbol"], esppSalaryEquiv)
        return esppSalaryEquiv


    def doRsuCalc(self, stockQuote, usdToCurrency):
        conf = self.config
        rsuSharesPerYear = int(conf["rsuTotalShares"] / conf["rsuVestingYears"])
        rsuValueUsd = rsuSharesPerYear * stockQuote
        rsuSalaryEquiv = rsuValueUsd * conf["employersNicFactor"] * usdToCurrency
        LOGGER.info("You vest %d shares per year worth $%.2f, equivalent to %s%.2f as salary",
            rsuSharesPerYear, rsuValueUsd, conf["currencySymbol"], rsuSalaryEquiv)
        return rsuSalaryEquiv

    def doCalc(self):
        conf = self.config

        quoteUrl = conf["quoteUrl"].replace("$SYMBOL", conf["symbol"]).replace("$QUOTETOKEN", conf["quoteToken"])
        LOGGER.debug("Fetching %s", quoteUrl)
        resp = requests.get(quoteUrl, timeout=10)
        if not resp.ok:
            LOGGER.error("Quote request failed: %s", pformat(resp.__dict__))
            resp.raise_for_status()
        currentQuote = resp.json()["c"]
        LOGGER.debug("Current quote for %s: %f", conf["symbol"], currentQuote)

        rateUrl = conf["rateUrl"].replace("$CURRENCY", conf["currency"]).replace("$RATETOKEN", conf["rateToken"])
        LOGGER.debug("Fetching %s", rateUrl)
        resp = requests.get(rateUrl, timeout=10)
        if resp.ok:
            usdToCurrency = resp.json()["USD_%s" % conf["currency"]]
        else:
            LOGGER.error("Rate request failed: %s", pformat(resp.__dict__))
            rateUrl = conf["rateUrlBackup"].replace("$CURRENCY", conf["currency"]).replace("$QUOTETOKEN", conf["quoteToken"])
            LOGGER.debug("Fetching back rate URL %s", rateUrl)
            rateResp = requests.get(rateUrl, timeout=10)
            if not rateResp.ok:
                LOGGER.error("Backup rate request failed: %s", pformat(rateResp.__dict__))
                rateResp.raise_for_status()
            usdToCurrency = 1 / rateResp.json()["quote"]["USD"]

        LOGGER.debug("Current rate for %s: %s1.00 = $%.3f (1/%.3f)", conf["currency"], conf["currencySymbol"], usdToCurrency, 1.0/usdToCurrency)

        # targetUrl = conf["targetUrl"].replace("$SYMBOL", conf["symbol"]).replace("$QUOTETOKEN", conf["quoteToken"])
        # LOGGER.debug("Fetching %s", targetUrl)
        # resp = requests.get(targetUrl, timeout=10)
        # if not resp.ok:
        #     LOGGER.error("Target request failed: %s", pformat(resp.__dict__))
        #     resp.raise_for_status()
        # respJson = resp.json()
        # targetHigh = respJson["targetHigh"]
        # targetLow = respJson["targetLow"]
        # targetMean = respJson["targetMean"]
        # targetMedian = respJson["targetMedian"]
        # LOGGER.debug("Targets for %s: High $%.2f, Low $%.2f, Mean $%.2f, Median $%.2f", conf["symbol"], targetHigh, targetLow, targetMean, targetMedian)

        # Values below are annual
        salaryAndBonus = conf["baseSalary"] + conf["bonus"]

        # Stock numbers assume that its quote stays where it is
        
        rsuSalaryEquiv = self.doRsuCalc(currentQuote, usdToCurrency)
        # aspRsuSalaryEquiv = self.doRsuCalc(targetHigh, usdToCurrency)
        # expectRsuSalaryEquiv = self.doRsuCalc(targetMedian, usdToCurrency)
        
        esppSalaryEquiv = self.doEsppCalc(conf["esppBuyPriceUsd"], salaryAndBonus, currentQuote, usdToCurrency)
        deservedEsppSalaryEquiv = self.doEsppCalc(conf["deservedEsppBuyPriceUsd"], salaryAndBonus, currentQuote, usdToCurrency)
        # aspEsppSalaryEquiv = self.doEsppCalc(conf["esppBuyPriceUsd"], salaryAndBonus, targetHigh, usdToCurrency)
        # aspDeservedEsppSalaryEquiv = self.doEsppCalc(conf["deservedEsppBuyPriceUsd"], salaryAndBonus, targetHigh, usdToCurrency)
        # expectEsppSalaryEquiv = self.doEsppCalc(conf["esppBuyPriceUsd"], salaryAndBonus, targetMedian, usdToCurrency)
        # expectDeservedEsppSalaryEquiv = self.doEsppCalc(conf["deservedEsppBuyPriceUsd"], salaryAndBonus, targetMedian, usdToCurrency)

        totalSalaryEquiv = salaryAndBonus + rsuSalaryEquiv + esppSalaryEquiv
        deservedTotalSalaryEquiv = salaryAndBonus + rsuSalaryEquiv + deservedEsppSalaryEquiv
        # aspTotalSalaryEquiv = salaryAndBonus + aspRsuSalaryEquiv + aspEsppSalaryEquiv
        # aspDeservedTotalSalaryEquiv = salaryAndBonus + aspRsuSalaryEquiv + aspDeservedEsppSalaryEquiv
        # expectTotalSalaryEquiv = salaryAndBonus + expectRsuSalaryEquiv + expectEsppSalaryEquiv
        # expectDeservedTotalSalaryEquiv = salaryAndBonus + expectRsuSalaryEquiv + expectDeservedEsppSalaryEquiv

        LOGGER.info("Your total salary is %s%.2f", conf["currencySymbol"], salaryAndBonus)
        LOGGER.info("Total equivalent salary: %s%.2f", conf["currencySymbol"], totalSalaryEquiv)

        return {
            # "AspirationalTotalSalaryEquivalent": aspTotalSalaryEquiv,
            # "AspirationalEsppSalaryEquivalent": aspEsppSalaryEquiv,
            # "AspirationalRsuSalaryEquivalent": aspRsuSalaryEquiv,
            # "AspirationalDeservedTotalSalaryEquivalent": aspDeservedTotalSalaryEquiv,
            # "AspirationalDeservedEsppSalaryEquivalent": aspDeservedEsppSalaryEquiv,
            # "AspirationalDeservedRsuSalaryEquivalent": aspRsuSalaryEquiv,
            "DeservedTotalSalaryEquivalent": deservedTotalSalaryEquiv,
            "DeservedEsppSalaryEquivalent": deservedEsppSalaryEquiv,
            "DeservedRsuSalaryEquivalent": rsuSalaryEquiv,
            "EsppSalaryEquivalent": esppSalaryEquiv,
            # "ExpectedDeservedTotalSalaryEquivalent": expectDeservedTotalSalaryEquiv,
            # "ExpectedDeservedEsppSalaryEquivalent": expectDeservedEsppSalaryEquiv,
            # "ExpectedDeservedRsuSalaryEquivalent": expectRsuSalaryEquiv,
            # "ExpectedTotalSalaryEquivalent": expectTotalSalaryEquiv,
            # "ExpectedEsppSalaryEquivalent": expectEsppSalaryEquiv,
            # "ExpectedRsuSalaryEquivalent": expectRsuSalaryEquiv,
            "RsuSalaryEquivalent": rsuSalaryEquiv,
            "SalaryAndBonus": salaryAndBonus,
            "StockPrice": currentQuote,
            # "StockPriceTargetHigh": targetHigh,
            # "StockPriceTargetLow": targetLow,
            # "StockPriceTargetMean": targetMean,
            # "StockPriceTargetMedian": targetMedian,
            "TotalSalaryEquivalent": totalSalaryEquiv,
            "UsdToGbp": 1 / usdToCurrency
        }


    def calcResult(self, timeNow):
        LOGGER.info("Creating record at %s", timeNow)
        for i in itertools.count():
            try:
                calcResult = self.doCalc()
                return calcResult
            except Exception as e:
                if i < 10:
                    LOGGER.warning("Failed to calculate result, will retry: %s", e)
                else:
                    LOGGER.error("Failed to calculate result, giving up: %s", e)
                    return None
            time.sleep(1)


    def createRecord(self, timeNow, calcResult):
        outputPath = self.config["outputPath"]
        if outputPath:
            jsonCalcElems = ['"%s":%.3f' % (k, v) for k, v in calcResult.items()]
            logLine = '{"timestamp":"%sZ",%s}\n' % (timeNow.replace(microsecond=0), ",".join(jsonCalcElems))
            for i in itertools.count():
                try:
                    with open(outputPath, "a") as fp:
                        fp.write(logLine)
                    LOGGER.debug("Appended record to %s: %s", outputPath, logLine)
                    break
                except Exception as e:
                    if i < 10:
                        LOGGER.warning("Failed to append to file %s, will retry: %s", outputPath, e)
                    else:
                        LOGGER.error("Failed to append to file %s, giving up: %s", outputPath, e)
                        return
                time.sleep(1)


    def updateMeters(self, timeNow, calcResult):
        LOGGER.info("Updating meters at %s", timeNow)
        for k, v in calcResult.items():
            if k != "timestamp":
                if k not in self.valueRecorders:
                    self.valueRecorders[k] = self.meter.create_valuerecorder(
                        description=self.DESCRIPTIONS[k],
                        name=k,
                        unit=self.UNITS[k],
                        value_type=float
                    )
                labels = {}
                self.valueRecorders[k].record(v, labels)


    def enter(self):
        LOGGER.info("Entered WedgeMonitorApp")
        while True:
            timeNow = datetime.utcnow()
            secondNow = timeNow.second
            minuteNow = timeNow.minute
            if self.lastMinute != minuteNow and secondNow >= 30 and self.lastSecond < 30:
                calcResult = self.calcResult(timeNow)
                self.updateMeters(timeNow, calcResult)
                self.createRecord(timeNow, calcResult)
                self.lastMinute = minuteNow
            self.lastSecond = secondNow
            time.sleep(1.01 - (timeNow.microsecond / 1e7)) # Home in on exact second tickover


if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.DEBUG)
    app = WedgeMonitorApp()
    app.loadConfig()
    app.startExporter()
    app.enter()
