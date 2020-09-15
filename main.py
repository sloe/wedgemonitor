
from datetime import datetime
import itertools
import json
import logging
from pprint import pformat
import requests
import time

LOGGER=logging.getLogger('main')

class WedgeMonitorApp(object):
    def __init__(self):
        self.lastMinute = 0
        self.lastSecond = 0


    def loadConfig(self):
        configFilename = "config.json"
        with open(configFilename) as fp:
            self.config = json.load(fp)
        LOGGER.info("Loaded config %s", self.config)


    def doCalc(self):
        conf = self.config
        currSym = conf["currencySymbol"]

        quoteUrl = conf["quoteUrl"].replace("$SYMBOL", conf["symbol"]).replace("$QUOTETOKEN", conf["quoteToken"])
        LOGGER.debug("Fetching %s", quoteUrl)
        quoteResp = requests.get(quoteUrl)
        if not quoteResp.ok:
            LOGGER.error("Quote request failed: %s", pformat(quoteResp.__dict__))
            quoteResp.raise_for_status()
        currentQuote = quoteResp.json()["c"]
        LOGGER.debug("Current quote for %s: %f", conf["symbol"], currentQuote)

        rateUrl = conf["rateUrl"].replace("$CURRENCY", conf["currency"]).replace("$QUOTETOKEN", conf["quoteToken"])
        LOGGER.debug("Fetching %s", rateUrl)
        rateResp = requests.get(rateUrl)
        if not rateResp.ok:
            LOGGER.error("Rate request failed: %s", pformat(rateResp.__dict__))
            rateResp.raise_for_status()
        usdToCurrency = 1 / rateResp.json()["quote"]["USD"]
        LOGGER.debug("Current rate for %s: %s1.00 = $%.2f (1/%.2f)", conf["currency"], currSym, usdToCurrency, 1.0/usdToCurrency)

        # Values below are annual
        salaryAndBonus = conf["baseSalary"] + conf["bonus"]

        # Stock numbers assume that its quote stays where it is
        rsuSharesPerYear = int(conf["rsuTotalShares"] / conf["rsuVestingYears"])
        rsuValueUsd = rsuSharesPerYear * currentQuote
        rsuSalaryEquiv = rsuValueUsd * conf["employersNicFactor"] * usdToCurrency

        esppBuyPrice = min(conf["esppBuyPriceUsd"], currentQuote) * conf["esppDiscountFactor"]
        esppBuyAmountUsd = min(salaryAndBonus * conf["esppSalaryFactor"] / usdToCurrency, conf["esppLimitUsd"]) * conf["esppDiscountFactor"]
        esppShareCount = int(esppBuyAmountUsd / esppBuyPrice)
        esppProfitAmount = esppShareCount * (currentQuote - esppBuyPrice)
        esppSalaryEquivUsd = esppProfitAmount * conf["employersNicFactor"]
        esppSalaryEquiv = esppSalaryEquivUsd * usdToCurrency

        totalSalaryEquiv = salaryAndBonus + rsuSalaryEquiv + esppSalaryEquiv

        LOGGER.info("Your total salary is %s%.2f", currSym, salaryAndBonus)
        LOGGER.info("You vest %d shares per year worth $%.2f, equivalent to %s%.2f as salary",
                    rsuSharesPerYear, rsuValueUsd, currSym, rsuSalaryEquiv)
        LOGGER.info("You purchase %d ESPP shares per year with $%.2f, with profit equivalent to %s%.2f as salary",
                    esppShareCount, esppBuyAmountUsd, currSym, esppSalaryEquiv)

        LOGGER.info("Total equivalent salary: %s%.2f", currSym, totalSalaryEquiv)

        return {
            "Stock price": currentQuote,
            "%s to USD" % conf["currency"]: 1 / usdToCurrency,
            "Salary and bonus": salaryAndBonus,
            "RSU salary equivalent": rsuSalaryEquiv,
            "ESPP salary equivalent": esppSalaryEquiv,
            "Total salary equivalent": totalSalaryEquiv
        }


    def createRecord(self, timeNow):
        LOGGER.info("Creating record at %s", timeNow)
        for i in itertools.count():
            try:
                calcResult = self.doCalc()
                break
            except Exception as e:
                if i < 10:
                    LOGGER.warning("Failed to calculate result, will retry: %s", e)
                else:
                    LOGGER.error("Failed to calculate result, giving up: %s", e)
                    return
            time.sleep(1)

        outputPath = self.config["outputPath"]
        if outputPath:
            jsonCalcElems = ['"%s":%.2f' % (k, v) for k, v in calcResult.items()]
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


    def enter(self):
        LOGGER.info("Entered WedgeMonitorApp")
        while True:
            timeNow = datetime.utcnow()
            secondNow = timeNow.second
            minuteNow = timeNow.minute
            if self.lastMinute != minuteNow and secondNow >= 30 and self.lastSecond < 30:
                self.createRecord(timeNow)
                self.lastMinute = minuteNow
            self.lastSecond = secondNow
            time.sleep(1.01 - (timeNow.microsecond / 1e7)) # Home in on exact second tickover


if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.DEBUG)
    app = WedgeMonitorApp()
    app.loadConfig()
    app.enter()

 
	
	
	
