from random import choice, uniform, randint, random
from tls_client import Session
from requests import get, post
from time import sleep, time
from re import search

from modules.retry import retry, have_json, CustomError
from modules.database import DataBase
from modules.utils import logger
from modules import config
import settings


class Browser:
    def __init__(self, db: DataBase, encoded_pk: str, proxy: str):
        self.max_retries = 5
        self.db = db
        self.encoded_pk = encoded_pk
        if proxy == "mobile":
            self.proxy = settings.PROXY
        else:
            logger.debug(f'[•] Soft | Got proxy {proxy}')
            self.proxy = proxy

        if self.proxy not in ['http://log:pass@ip:port', '', None]:
            if proxy == "mobile": self.change_ip()
        else:
            logger.warning(f'[-] Soft | You dont use proxies!')

        self.session = self.get_new_session()
        self.session.headers.update({
            "Origin": "https://bartio.faucet.berachain.com",
            "Referer": "https://bartio.faucet.berachain.com/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        })
        self.address = None


    def get_new_session(self):
        session = Session(
            client_identifier="safari_16_0",
            random_tls_extension_order=True
        )

        if self.proxy not in ['http://log:pass@ip:port', '', None]:
            session.proxies.update({'http': self.proxy, 'https': self.proxy})

        return session


    @have_json
    def send_request(self, **kwargs):
        if kwargs.get("method"): kwargs["method"] = kwargs["method"].upper()
        return self.session.execute_request(**kwargs)


    def change_ip(self):
        if settings.CHANGE_IP_LINK not in ['https://changeip.mobileproxy.space/?proxy_key=...&format=json', '']:
            print('')
            while True:
                try:
                    r = get(settings.CHANGE_IP_LINK)
                    if 'mobileproxy' in settings.CHANGE_IP_LINK and r.json().get('status') == 'OK':
                        logger.debug(f'[+] Proxy | Successfully changed ip: {r.json()["new_ip"]}')
                        return True
                    elif not 'mobileproxy' in settings.CHANGE_IP_LINK and r.status_code == 200:
                        logger.debug(f'[+] Proxy | Successfully changed ip: {r.text}')
                        return True
                    logger.error(f'[-] Proxy | Change IP error: {r.text} | {r.status_code}')
                    sleep(10)

                except Exception as err:
                    logger.error(f'[-] Browser | Cannot get proxy: {err}')


    def solve_2captcha(self):
        def create_task():
            if self.proxy in ['http://log:pass@ip:port', '', None]:
                payload_kwargs = {"type": "TurnstileTaskProxyless"}
            else:
                proxy_type, user, psw, ip, port = self.proxy.replace('://', ':').replace('@', ':').split(':')
                payload_kwargs = {
                    "type": "TurnstileTask",
                    "proxyType": proxy_type,
                    "proxyAddress": ip,
                    "proxyPort": port,
                    "proxyLogin": user,
                    "proxyPassword": psw,
                }
            payload = {
                "clientKey": settings.CAPTCHA_KEY,
                "task": {
                    "websiteURL": "https://artio.faucet.berachain.com/",
                    "websiteKey": "0x4AAAAAAARdAuciFArKhVwt",
                    **payload_kwargs
                }
            }

            r = post(f'https://{api_url}/createTask', json=payload)
            if r.json().get('taskId'):
                return r.json()['taskId']
            else:
                raise Exception(f'Faucet Captcha error: {r.text}')

        def get_task_result(task_id: str):
            payload = {
                "clientKey": settings.CAPTCHA_KEY,
                "taskId": task_id
            }
            r = post(f'https://{api_url}/getTaskResult', json=payload)

            if r.json().get('status') in ['pending', 'processing']:
                sleep(3)
                return get_task_result(task_id=task_id)
            elif r.json().get('status') == 'ready':
                logger.info(f'[+] Faucet | Captcha solved')
                return r.json()['solution']['token']
            else:
                raise Exception(f'Couldnt solve captcha for Faucet: {r.text}')

        api_url = 'api.2captcha.com'
        task_id = create_task()
        logger.info(f'[•] Faucet | Waiting for solve captcha')
        return get_task_result(task_id=task_id)


    @retry(source="Bera", module_str="Drip tokens", exceptions=Exception)
    def drip_tokens(self):
        captcha = self.solve_2captcha()

        r = self.session.post(
            f"https://bartiofaucet.berachain.com/api/claim?address={self.address}",
            json={"address": self.address},
            headers={"Authorization": f"Bearer {captcha}"}
        )

        if r.text.strip().isdigit():
            logger.error(f'[-] Bera | Wallet has no ETH in Ethereum to drip')
            self.db.append_report(privatekey=self.encoded_pk, text=f"Wallet has no ETH in Ethereum to drip", success=False)

        elif r.json().get("msg") == f"Added {self.address} to the queue":
            r = self.send_request(
                method="get",
                url="https://bartiofaucet.berachain.com/api/info"
            )
            queue = r.json()["queue_size"]
            amount = int(r.json()["payout"].removesuffix("Gwei")) / 1e9
            logger.info(f'[+] Faucet | Dripped {amount} BERA. Queue: {queue}')
            self.db.append_report(privatekey=self.encoded_pk, text=f"Dripped {amount} BERA", success=True)
            return {"success": True}

        elif "You have exceeded the rate limit" in r.json().get("msg"):
            cd = r.json()["msg"].split('. ')[1].split(' ')[2]

            parsed_cd = {
                "hours": int(search(r"\dh", cd)[0][:-1]) if "h" in cd else 0,
                "minutes": int(search(r"\d+m", cd)[0][:-1]) if "m" in cd else 0,
                "seconds": int(search(r"\d+s", cd)[0][:-1]) if "s" in cd else 0,
            }
            cd_timestamp = int(time() + parsed_cd["hours"] * 60 * 60 + parsed_cd["minutes"] * 60 + parsed_cd["seconds"])

            logger.warning(f'[-] Faucet | Cooldown to drip BERA: {cd}')
            self.db.append_report(privatekey=self.encoded_pk, text=f"Cooldown to drip BERA: {cd}", success=False)
            return {"success": False, "cd": cd_timestamp}

        else:
            logger.error(f'[-] Bera | Drip tokens error: {r.json()}')

        return {"success": False, "cd": int(time())}


    @retry(source="Bex", module_str="Get swap data", exceptions=Exception)
    def get_bex_data(self, token_value: int):
        params = {
            "fromAsset": config.TOKEN_ADDRESSES["WBERA"],
            "toAsset": config.TOKEN_ADDRESSES["HONEY"],
            "amount": token_value,
        }
        r = self.send_request(
            method="get",
            url="https://bartio-bex-router.berachain.com/dex/route",
            params=params
        )
        return r.json()["steps"][0]


    @retry(source="Browser", module_str="Get random validator", exceptions=Exception)
    def get_validator(self):
        params = {
            "sortBy": "apy",
            "sortOrder": "desc",
            "page": 1,
            "pageSize": 200,
        }
        r = self.send_request(
            method="get",
            url="https://bartio-pol-indexer.berachain.com/berachain/v1alpha1/beacon/validators",
            params=params
        )
        validators = [
            {
                "name": v["metadata"]["name"],
                "apy": v["apy"],
                "rewardRate": v["rewardRate"],
                "id": v["id"],
            } for v in r.json()["validators"]
            if (
                v["active"] and
                v["commission"] * 100 <= 5
            )
        ]
        sorted_apy = sorted(validators, key=lambda k: k['apy'], reverse=True)
        avg_apy = sum([v["apy"] for v in sorted_apy]) / len(sorted_apy)
        good_apy_validators = [v for v in sorted_apy if v["apy"] >= avg_apy]
        validator_index = round(random() * random() * random() * len(good_apy_validators))
        return validators[validator_index]["id"]


    @retry(source="Browser", module_str="Get wallet delegates", exceptions=Exception)
    def get_delegates(self, current_block: int):
        r = self.send_request(
            method="get",
            url=f"https://bartio-pol-indexer.berachain.com/berachain/v1alpha1/beacon/user/{self.address}/validators",
        )
        awaiting_delegates = [delegate for delegate in r.json()["userValidators"] if
                delegate["userValidator"]["amountQueued"] != "0"
        ]
        delegates = [{
            "id": delegate["validator"]["id"],
            "name": delegate["validator"]["metadata"]["name"] if delegate["validator"]["metadata"] else f'{delegate["validator"]["id"][:4]}...{delegate["validator"]["id"][-4:]}',
        } for delegate in awaiting_delegates if
                     int(delegate["userValidator"]["latestBlock"]) + 2 ** 13 + 8 < current_block
        ]

        return {"awaiting": len(awaiting_delegates), "delegates": delegates}


    @retry(source="Browser", module_str="Get relay tx", exceptions=Exception)
    def get_relay_tx(self, min_out: float, balance: float, from_chain_id: int, to_chain_id: int, retry=0):
        value = int(min_out * 1e18)
        headers = {
            "Origin": "https://relay.link",
            "Referer": "https://relay.link/"
        }
        payload = {
            "user": self.address,
            "originChainId": from_chain_id,
            "originCurrency": "0x0000000000000000000000000000000000000000",
            "destinationChainId": to_chain_id,
            "destinationCurrency": "0x0000000000000000000000000000000000000000",
            "recipient": self.address,
            "amount": str(value),
            "useExternalLiquidity": False,
            "referrer": "relay.link/swap",
            "tradeType": "EXACT_INPUT",
        }

        for i in range(2):
            r = post('https://api.relay.link/quote', json=payload, headers=headers)

            if i == 0:
                fee = int(r.json()["fees"]["relayer"]["amount"])
                gas = int(r.json()["fees"]["gas"]["amount"])
                total_fee = int(fee * 1.01 + gas * 1.2)
                min_send = min_out + total_fee * 1.05 / 1e18

                if balance < min_send:
                    raise CustomError(f'Couldnt relay bridge from {from_chain_id} to ethereum: not enough balance: {round(balance, 6)} less than {round(min_send, 6)}')
                amount = round(uniform(min_send, min(balance, min_send + 0.0005)), randint(4, 7))

                payload["amount"] = str(int(amount * 1e18))
            elif i == 1:
                return r.json()['steps'][0]['items'][0]['data']


    @retry(source="Kodiak", module_str="Get swap data", exceptions=Exception)
    def get_kodiak_data(self, token_value: int):
        params = {
            "protocols": "v2,v3",
            "tokenInAddress": config.TOKEN_ADDRESSES["HONEY"],
            "tokenInChainId": 80084,
            "tokenOutAddress": config.TOKEN_ADDRESSES["iBGT"],
            "tokenOutChainId": 80084,
            "amount": token_value,
            "type": "exactIn"
        }
        r = self.send_request(
            method="get",
            url="https://ebey72gfe6.execute-api.us-east-1.amazonaws.com/prod/quote",
            params=params
        )
        if r.json().get("route") is None:
            raise CustomError(f'bad response for swap {round(token_value / 1e18, 6)} HONEY: {r.json()}')

        tx_type = r.json()["route"][0][0]["type"]

        if tx_type == "v3-pool":
            path = ""
            for index, route in enumerate(r.json()["route"][0]):
                if index == 0:
                    path += route["tokenIn"]["address"]
                path += hex(int(route["fee"]))[2:].zfill(6)
                path += route["tokenOut"]["address"][2:]
            # path = path.encode()

        elif tx_type == "v2-pool":
            path = []
            for index, route in enumerate(r.json()["route"][0]):
                if index == 0: params = ["tokenIn", "tokenOut"]
                else: params = ["tokenOut"]
                path += [route[param]["address"] for param in params]

        value_in = int(int(r.json()["quoteGasAdjusted"]) * 0.999)

        return {
            "valueIn": value_in,
            "amountIn": round(float(r.json()["quoteGasAdjustedDecimals"]), 4),
            "type": tx_type,
            "path": path,
        }
