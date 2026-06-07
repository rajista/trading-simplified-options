import requests
import json


def fetch_nifty_option_chain():
    base_url = "https://www.nseindia.com"
    option_chain_page = f"{base_url}/option-chain?type=Indices&symbol=NIFTY"
    contract_info_url = f"{base_url}/api/option-chain-contract-info"
    option_chain_url = f"{base_url}/api/option-chain-v3"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": option_chain_page,
    }

    try:
        with requests.Session() as session:
            # Initialize the cookies expected by NSE's API.
            session.get(option_chain_page, headers=headers, timeout=15).raise_for_status()

            contract_response = session.get(
                contract_info_url,
                params={"symbol": "NIFTY"},
                headers=headers,
                timeout=15,
            )
            contract_response.raise_for_status()
            expiry_dates = contract_response.json().get("expiryDates", [])
            if not expiry_dates:
                raise ValueError("NSE returned no NIFTY expiry dates")

            response = session.get(
                option_chain_url,
                params={
                    "type": "Indices",
                    "symbol": "NIFTY",
                    "expiry": expiry_dates[0],
                },
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            contracts = data.get("records", {}).get("data", [])
            if not contracts:
                raise ValueError("NSE returned an empty option chain")

            return contracts

    except (requests.RequestException, ValueError, json.JSONDecodeError) as error:
        print(f"Error fetching option chain: {error}")
        return None


if __name__ == "__main__":
    chain_data = fetch_nifty_option_chain()
    if chain_data:
        print(f"Found {len(chain_data)} active contracts.")
        print(json.dumps(chain_data[0], indent=2))
