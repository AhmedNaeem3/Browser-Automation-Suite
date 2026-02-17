import csv
import os
import random
import time
from bs4 import BeautifulSoup
from logger import setup_logging
from twocaptcha import TwoCaptcha
from playwright.sync_api import sync_playwright, Page
from playwright_stealth import Stealth


class AttorneyDetailsScraper:

    def __init__(self):
        self.base_url = "https://iapps.courts.state.ny.us"
        self.search_url = "https://iapps.courts.state.ny.us/attorneyservices"
        self.logger = setup_logging()
        self.cdp_url = os.getenv("CDP_URL")
        self.solver = TwoCaptcha(self.api_key)

    def connect_and_setup(self, p: sync_playwright) -> Page:
        """
        Establishes a connection to an active Chrome instance via CDP.

        This method attaches to an existing browser context and initializes a new
        page session. It incorporates the Playwright Stealth plugin to obfuscate
        automation footprints and bypass fingerprinting, ensuring the session
        maintains a high browser trust score.
        """

        self.logger.info("Attempting to connect to running Chrome instance.")
        browser = p.chromium.connect_over_cdp(self.cdp_url)
        context = browser.contexts[0]
        stealth = Stealth()
        stealth.apply_stealth_sync(context)
        page = context.new_page()
        self.logger.info(
            f"Successfully connected and created new tab. Navigating to {self.search_url}"
        )
        return page

    def get_attorneys_list(self):
        """
        Parses 'scraped_data.csv' to retrieve a deduplicated list of attorney names.
        Returns:
            list: Unique attorney names extracted from the csv file.
        """
        file_path = "scraped_data.csv"
        attorney_list = []

        try:
            with open(file_path, mode="r", encoding="utf-8") as csvfile:
                reader = csv.DictReader(csvfile)

                attorney_list = set(
                    row["Estate Attorney"].strip()
                    for row in reader
                    if row.get("Estate Attorney")
                )

        except FileNotFoundError:
            print(f"Error: {file_path} does not exist.")
        except KeyError:
            print("Error: The column 'Estate Attorney' was not found in the CSV.")

        return list(attorney_list)

    def run(self):
        """
        Main execution loop for processing attorney searches and data extraction.

        This method normalizes attorney names by removing specific suffixes/prefixes
        to ensure form compatibility. It implements human-mimicry via randomized
        delays and manual Captcha intervention. The search logic utilizes a custom
        matching algorithm that prioritizes exact string matches within result sets,
        defaulting to the primary result when an exact match is unavailable.

        Additionally, it identifies search queries that return no results,
        logging these instances as warnings and skipping the records to maintain
        data integrity within the final log and CSV output.
        """

        self.logger.info("Starting the New York Court script...")
        try:
            with sync_playwright() as p:
                page = self.connect_and_setup(p)
                attorneys_list = self.get_attorneys_list()
                page.goto(self.search_url, timeout=60000, wait_until="domcontentloaded")

                suffixes_and_prefixes = [
                    "JR",
                    "SR",
                    "II",
                    "III",
                    "IV",
                    "V",
                    "ESQ",
                    "ESQUIRE",
                    "VAN",
                    "DE",
                    "DEL",
                    "DI",
                    "LA",
                    "ST",
                    "VON",
                ]
                for attorney in attorneys_list:

                    scraped_attorney = []
                    try:
                        self.logger.info(f"Searching Details for attorney: {attorney}")
                        first_name = None
                        middle_name = None
                        last_name = None
                        if "O Connor" in attorney:
                            attorney = attorney.replace("O Connor", "O'Connor")
                        attorney_name = attorney.split(" ")

                        cleaned_name = [
                            p
                            for p in attorney_name
                            if p.strip() and p.upper() not in suffixes_and_prefixes
                        ]
                        if len(cleaned_name) == 2:
                            first_name = cleaned_name[0]
                            last_name = cleaned_name[1]

                        if len(cleaned_name) == 3 or len(cleaned_name) == 4:
                            first_name = cleaned_name[0]
                            middle_name = cleaned_name[1]
                            last_name = cleaned_name[2]

                        if first_name:
                            locate_first_name = page.locator(
                                'input[name="wmcSearchTabs:pnlAttorneySearch:nameSearchPanel:strFirstName"]'
                            )
                            locate_first_name.clear()
                            locate_first_name.press_sequentially(
                                first_name,
                                delay=random.randint(80, 180),
                            )
                        locate_last_name = page.locator(
                            'input[name="wmcSearchTabs:pnlAttorneySearch:nameSearchPanel:strLastName"]'
                        )
                        locate_last_name.clear()
                        if last_name:

                            locate_last_name.press_sequentially(
                                last_name,
                                delay=random.randint(80, 180),
                            )
                        locate_middle_name = page.locator(
                            'input[name="wmcSearchTabs:pnlAttorneySearch:nameSearchPanel:strMiddleName"]'
                        )
                        locate_middle_name.clear()
                        if middle_name:
                            locate_middle_name.press_sequentially(
                                middle_name,
                                delay=random.randint(80, 180),
                            )

                        captcha = page.locator(".h-captcha")
                        if captcha.is_visible():
                            self.logger.info("!!! CAPTCHA DETECTED !!!")
                            self.logger.info(
                                "Please resolve the captcha and then click on search."
                            )

                        page.locator('.BTN_Green[name="btnSubmit"]').click()

                        time.sleep(2)
                        no_results = page.locator(".CONT_MsgBox_Error")
                        if no_results.count() > 0 and no_results.is_visible():
                            if (
                                "Your Attorney search returned no results."
                                in no_results.inner_text()
                            ):
                                self.logger.warning(
                                    f"No results found for attorney: {attorney} . Skipping this attorney!"
                                )
                                continue

                        page.wait_for_selector(".STR_Visited", timeout=90000)
                        links = page.locator(".STR_Visited").all()
                        target_link = None

                        search_name_upper = attorney.upper()

                        for link in links:
                            link_text = link.inner_text().upper()
                            attorney_name_parts = set(search_name_upper.split())
                            link_text_parts = set(link_text.replace(",", " ").split())
                            if attorney_name_parts == link_text_parts:
                                target_link = link
                                self.logger.info(f"Found exact match: {link_text}")
                                break

                        link_to_click = target_link if target_link else links[0]

                        if not target_link:
                            self.logger.warning(
                                f"No exact match found for {attorney}. Clicking first result: {links[0].inner_text()}"
                            )

                        with page.context.expect_page() as new_page_info:
                            link_to_click.click()

                        new_page = new_page_info.value
                        new_page.wait_for_load_state("networkidle")

                        self.logger.info(
                            f"Navigated to attorney details page URL: {new_page.url}"
                        )

                        response = new_page.content()
                        attorney_details = self.extract_attorney_details(response)

                        self.logger.info("Close the attorney details page.")
                        new_page.close()
                        self.logger.info("Successfully Extracted Attorney Details")
                        scraped_attorney.append(attorney_details)
                        page.go_back(timeout=60000)
                        self.logger.info("Now Saving attorney details to CSV")
                        self.save_to_csv(scraped_attorney)
                        self.logger.info("Navigated back to the form page")
                        time.sleep(1)

                    except Exception as e:
                        self.logger.error(f"Error processing attorney {attorney}: {e}")
                        continue

                self.logger.info(
                    "No more Attorneys left. Successfully Scraped all the attorneys contact details and save to CSV file."
                )
                self.logger.info(
                    "Proccess Finished: Browser Closed. Complete data saved to CSV."
                )
        except Exception as e:
            self.logger.error(f"\nFATAL ERROR during execution: {e}")

    def extract_attorney_details(self, response):
        """
        Parses the attorney profile HTML to extract structured contact information.

        This method utilizes BeautifulSoup to map specific labels to their corresponding
        data cells. It performs DOM traversal to locate key fields—including name,
        email, address, and phone—and returns a sanitized dictionary of the
        attorney's professional details.
        """
        soup = BeautifulSoup(response, "html.parser")
        details = {}

        target_fields = {
            "Estate Attorney": "Name:",
            "Email": "Email:",
            "Address": "Business Address:",
            "Phone": "Business Phone:",
        }

        all_spans = soup.select(".CONT_Default span")

        for key, label_text in target_fields.items():
            label_span = next(
                (s for s in all_spans if s.get_text(strip=True) == label_text), None
            )

            if label_span:
                value_span = label_span.find_next_sibling("span", class_="CONT_Cell")
                if value_span:
                    details[key] = value_span.get_text(separator=" ", strip=True)
                else:
                    details[key] = ""
            else:
                details[key] = ""

        if details:
            self.logger.info(
                f"Extracted the attorney: {details['Estate Attorney']} complete details"
            )
        return details

    def save_to_csv(self, scraped_data):
        """Saves the scraped data to a CSV file."""
        file_exists = os.path.exists("attorney_details.csv")

        try:
            with open(
                "attorney_details.csv", mode="a", newline="", encoding="utf-8"
            ) as csvfile:
                writer = csv.writer(csvfile)

                if not file_exists:
                    writer.writerow(
                        [
                            "Estate Attorney",
                            "Email",
                            "Address",
                            "Phone",
                        ]
                    )

                for item in scraped_data:
                    writer.writerow(
                        [
                            item.get("Estate Attorney", ""),
                            item.get("Email", ""),
                            item.get("Address", ""),
                            item.get("Phone", ""),
                        ]
                    )

            self.logger.info("Data successfully saved to attorney_details.csv")

        except Exception as e:
            self.logger.error(f"Error saving to CSV: {e}")


if __name__ == "__main__":
    scraper = AttorneyDetailsScraper()
    scraper.run()
