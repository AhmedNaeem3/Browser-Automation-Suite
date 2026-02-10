import csv
import os
import time
from typing import List, Dict
import requests
from bs4 import BeautifulSoup
from logger import setup_logging
from playwright.sync_api import sync_playwright

from twocaptcha import TwoCaptcha


class NorthCarolinaScraper:

    def __init__(self):
        self.base_url = "https://portal-nc.tylertech.cloud"
        self.search_url = "{base_url}/Portal/Home/Dashboard/29"
        self.api_key = os.getenv("API_KEY")
        self.solver = TwoCaptcha(self.api_key)
        self.logger = setup_logging()
        self.scraped_data = []

    def solve_recaptcha_v2(self, url, sitekey):
        """Sends sitekey and URL to 2Captcha and returns the g-recaptcha-response token."""
        self.logger.info("Sending reCAPTCHA V2 task to 2Captcha...")
        try:
            result = self.solver.recaptcha(sitekey=sitekey, url=url)
            self.logger.info("reCAPTCHA Solved. Token received.")
            return result.get("code")
        except Exception as e:
            self.logger.error(f"Error solving reCAPTCHA: {e}")
            return None

    def run(self):
        """
        Executes the main scraping workflow:
        - Launches browser and loads the court search page.
        - Iteratively generates record numbers and fills them into the search form.
        - Extracts the page’s reCAPTCHA site-key, sends it with the URL to 2Captcha,
        receives the solved token, and injects it into the hidden CAPTCHA field.
        - Submits the search request and waits for the results grid to load.
        - Sets the page size to 200 using page.evaluate(), required because the
        Kendo UI dropdown hides its <select> element and cannot be clicked normally.
        - Parses the loaded results, collects all case rows, and sends them to the
        case-scraper.
        - Loops through all records, navigating back between searches, and finally
        saves all scraped data to CSV.
        """
        record_num = 0
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            url = self.search_url.format(base_url=self.base_url)
            page.goto(url, wait_until="load")
            self.logger.info(f"Navigated to: {url}")

            while True:
                foramtted_record_num = f"{record_num:02d}"
                record_number = f"24E00{foramtted_record_num}*"
                self.logger.info(f"Searching Record Number {record_number}")
                response = page.content()
                soup = BeautifulSoup(response, "html.parser")

                data_site_key = soup.select_one(".g-recaptcha").get("data-sitekey")

                record_input = "#caseCriteria_SearchCriteria"

                page.fill(record_input, record_number)
                captcha_token = self.solve_recaptcha_v2(url, data_site_key)
                time.sleep(0.5)
                recaptcha_input = "#g-recaptcha-response"
                page.evaluate(
                    f"document.querySelector('{recaptcha_input}').value = '{captcha_token}';"
                )
                time.sleep(0.5)
                submit_button = "#btnSSSubmit"
                page.click(
                    selector=submit_button,
                    timeout=30000,
                )

                record_num += 2
                self.logger.info(
                    "Token injected successfully. Clicking on submit button"
                )
                self.logger.info(f"Navigation complete. Current URL: {page.url}")
                time.sleep(0.5)
                no_cases_locator = page.locator("#ui-tabs-1 .portlet-body p")
                no_cases_locator.wait_for(state="visible", timeout=90000)
                if no_cases_locator.is_visible():
                    message_text = no_cases_locator.inner_text().strip()

                    if "No cases match your search" in message_text:
                        self.logger.info(
                            f"No cases found for search query: {record_number}"
                        )
                        self.logger.info(
                            "Scraping process has completed. No further results are available."
                        )
                        break

                page.wait_for_selector("#CasesGrid tbody tr", timeout=90000)

                dropdown_selector = "span.k-widget.k-dropdown span.k-dropdown-wrap"
                locate_dropdown = page.locator(dropdown_selector)

                if locate_dropdown.is_visible():
                    page.click(dropdown_selector)
                    time.sleep(0.5)

                    option_selector = 'li:has-text("200")'
                    option_element_handle = page.locator(
                        option_selector
                    ).element_handle()

                    if option_element_handle:

                        page.evaluate(
                            "element => element.click()", option_element_handle
                        )
                        self.logger.info(
                            "Pagination control interacted. Product view successfully updated to display the maximum capacity (200 items)."
                        )
                    else:
                        self.logger.error(
                            "Pagination failure: Required element for setting '200 items per page' was not found on the current view."
                        )
                else:
                    self.logger.info(
                        f"Dropdown not found may be records are less than 10 for search query: {record_number}"
                    )
                html = page.locator("#CasesGrid").inner_html()
                cases_soup = BeautifulSoup(html, "html.parser")
                all_cases = cases_soup.select(".k-master-row")

                self.scrape_cases(all_cases)
                self.logger.info(
                    f"Successfully scraped complete data of search query: {record_number}. Now saving the data in csv file."
                )
                page.go_back()
                self.logger.info(f"Navigated Back to: {url}")
                self.logger.info("Now searching next record number.")

            self.save_to_csv(self.scraped_data)

    def scrape_cases(self, all_cases):
        """
        Iterates through all scraped case entries, extracts each case’s ID,
        builds required API endpoints, determines case type, and skips cases
        that match exclusion criteria. Fetches attorney details and PDF
        availability through API calls, then compiles the finalized structured
        record for each valid case and adds it to the master dataset.
        """
        records = 0
        for case in all_cases:
            case_url = case.select_one(".caseLink").get("data-url")
            case_no = case.select_one(".caseLink").get_text(strip=True)
            case_api_id = case_url.split("?id=")[1].split("&")[0]

            case_pdf_api = f"{self.base_url}/app/RegisterOfActionsService/CaseEvents('{case_api_id}')?mode=portalembed&$top=50&$skip=0"
            complete_case_url = f"{self.base_url}/app/RegisterOfActionsService/Parties('{case_api_id}')?mode=portalembed&$top=50&$skip=0"
            case_type_url = f"{self.base_url}/app/RegisterOfActionsService/CaseSummariesSlim?key={case_api_id}"

            self.logger.info(
                f"Sending Request to check case type from API: {case_type_url}"
            )
            case_type_response = requests.get(case_type_url)
            case_type_json = case_type_response.json()

            check_case_type = (
                case_type_json.get("CaseInformation", {})
                .get("CaseType", {})
                .get("Description")
            )
            if check_case_type == "Decedents' Estate - Small Estate":
                self.logger.info(
                    f"Skipping the Case {case_no} because Case Type is: {check_case_type}"
                )
                continue

            attorney_info = self.get_attorney_info(complete_case_url)
            pdf_found = self.get_pdf_files(case_pdf_api, case_no)
            new_dict = {
                "Case Number": case_no,
                "Case Type": check_case_type,
                "Attorney Info": attorney_info,
                "PDF File": "Found" if pdf_found else "Not Found",
            }
            self.logger.info(
                f"Successfully Scraped the case no: {case_no} and its URL: {self.base_url}/{case_url}"
            )
            print(new_dict)
            self.scraped_data.append(new_dict)
            records += 1

    def get_pdf_files(self, case_pdf_api, case_no):
        """
        Requests the case events API to extract document metadata, then searches
        specifically for Bond-related documents. For each matching document, it
        retrieves the document fragment ID, type, name, and parent-case identifiers,
        and uses these values to construct the full PDF download URL. Ensures the
        same file is not downloaded twice, creates case-specific folders,
        downloads each PDF into its corresponding directory, and returns whether
        any document was successfully retrieved.
        """
        try:
            self.logger.info(f"For PDF URL info Sending Request to API: {case_pdf_api}")
            response = requests.get(case_pdf_api)
            self.logger.info(
                "Request Successful Now Extracting relevant document name, type, id's to construct the PDF URL."
            )
            json_data = response.json()

            pdf_found = False
            files_downloaded = []
            all_case_events = json_data.get("Events", [])
            if all_case_events:
                for case_event in all_case_events:
                    event_name = (
                        case_event.get("Event", {}).get("TypeId", {}).get("Description")
                    )
                    if "Bond" in event_name:
                        event = case_event.get("Event", {}).get("Documents", [])
                        if event:
                            event = event[0]
                        else:
                            continue
                        doc_id = (
                            event.get("DocumentVersions", [])[0]
                            .get("DocumentFragments", [])[0]
                            .get("DocumentFragmentID")
                        )

                        doc_data = event.get("DocumentTypeID", {})
                        doc_type_id = doc_data.get("CodeID")
                        doc_type = doc_data.get("Description")

                        doc_name = event.get("DocumentName")

                        event_name = event_name.replace("/", "-Or-")
                        formatted_file_name = event_name.replace(" ", "_").strip()
                        if formatted_file_name in files_downloaded:
                            self.logger.info(
                                f"{formatted_file_name} is already downloaded for case: {case_no}. Skipping this file."
                            )
                            continue
                        files_downloaded.append(formatted_file_name)
                        folder_name1 = "Scraped Data"
                        folder_name2 = "Scraped PDF's"
                        folder_name = os.path.join(folder_name1, folder_name2)
                        business_folder = os.path.join(folder_name, case_no)
                        os.makedirs(business_folder, exist_ok=True)

                        filename = f"{formatted_file_name}.pdf"
                        file_path = os.path.join(business_folder, filename)

                        parent_links = event.get("ParentLinks", [])
                        for link in parent_links:
                            location_id = link.get("NodeID")
                            case_id = link.get("ParentID")

                            case_pdf_url = f"{self.base_url}/Portal//DocumentViewer/DisplayDoc?documentID={doc_id}&caseNum={case_no}&locationId={location_id}&caseId={case_id}&docTypeId={doc_type_id}&isVersionId=false&docType={doc_type}&docName={doc_name}&eventName={event_name}"
                            self.logger.info(
                                f"Using different document name, type, id's and case ids. We have successfully constructed the pdf URL: {case_pdf_url}"
                            )
                            pdf_found = self.download_file(
                                case_pdf_url, file_path, filename
                            )
                            if pdf_found:
                                break
                            else:
                                self.logger.info(
                                    f"File Not downloaded. Trying with case id: {case_id} and location id: {location_id} now."
                                )
            if not pdf_found:
                self.logger.info(f"PDF not found for case: {case_no}")
            else:
                self.logger.info(f"PDF found for case: {case_no}")
            return pdf_found

        except requests.RequestException as e:
            self.logger.error(
                f"Request error when fetching case {case_no} PDF info: {e}."
            )
            return False

    def download_file(self, url, filepath, filename, retries=2):
        """
        Attempts to download a file from the specified URL and save
        it to the given local filepath. Streams the content in chunks
        to efficiently handle large files. Will retry the download up
        to 2 times in case of network or connection errors, logging each
        attempt.
        """

        for attempt in range(retries):
            try:
                with requests.get(url, stream=True, timeout=30) as res:
                    res.raise_for_status()
                    with open(filepath, "wb") as f:
                        for chunk in res.iter_content(8192):
                            if chunk:
                                f.write(chunk)
                        self.logger.info(f"Successfully Downloaded file: {filename}")
                return True
            except (
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
            ) as e:
                self.logger.error(
                    f"Download failed ({e}), retrying {attempt + 1}/{retries}..."
                )
                time.sleep(0.5)
        return False

    def get_attorney_info(self, case_url):
        """
        Retrieves attorney information for a given case by requesting the Parties API.
        Extracts attorney names along with all available address lines, formats each
        address into a clean string, builds dictionaries for each attorney, ensures
        duplicates are removed, and finally returns a compiled list of attorney
        dictionaries.
        """
        self.logger.info(f"Sending Request to get attorney info from API: {case_url}")
        try:
            response = requests.get(case_url)
            self.logger.info(f"Request Successfull to attorney info API: {case_url}")
            json_data = response.json()
            all_parties = json_data.get("Parties", [])
            attorney_info_len = 0
            attorneys = []
            for party in all_parties:
                attorney_info = party.get("CasePartyAttorneys", [])
                if attorney_info:
                    if len(attorney_info) > attorney_info_len:
                        attorney_info_len = len(attorney_info)
                        for info in attorney_info:
                            attorney_name = info.get("FormattedName")

                            addresses = info.get("Addresses", [])
                            attorney_addresses = []
                            if addresses:
                                for address in addresses:

                                    address_info1 = address.get("AddressLine1", "")
                                    address_info2 = address.get("AddressLine2", "")
                                    address_info3 = address.get("AddressLine3", "")
                                    address_info4 = address.get("AddressLine4", "")
                                    city = address.get("City", "")
                                    state = address.get("State", "")
                                    postal_code = address.get("PostalCode", "")

                                    location_parts = [
                                        part
                                        for part in [city, state, postal_code]
                                        if part
                                    ]

                                    if location_parts:

                                        state_zip = " ".join([state, postal_code])
                                        location_block = ", ".join([city, state_zip])
                                    else:
                                        location_block = ""

                                    full_address_components = [
                                        address_info1,
                                        address_info2,
                                        address_info3,
                                        address_info4,
                                        location_block,
                                    ]

                                    clean_parts = [
                                        part for part in full_address_components if part
                                    ]

                                    attorney_address = ", ".join(clean_parts)
                                    attorney_addresses.append(attorney_address)

                            attorney_complete_info = {
                                "AttorneyName": attorney_name,
                                "AttorneyAddress": (
                                    attorney_addresses if attorney_addresses else ""
                                ),
                            }
                            if not any(
                                key.get("AttorneyName") == attorney_name
                                for key in attorneys
                            ):
                                attorneys.append(attorney_complete_info)

            return attorneys
        except requests.RequestException as e:
            print(f"Request error when fetching attorney info: {e}")
            return []

    def _get_max_attorneys_in_batch(self, scraped_data):
        """
        Calculates the maximum number of attorney entries in the current batch.
        """

        max_attorneys = 0
        for item in scraped_data:
            attorney_info = item.get("Attorney Info", [])
            if isinstance(attorney_info, list):
                max_attorneys = max(max_attorneys, len(attorney_info))
        return max_attorneys

    def save_to_csv(self, scraped_data):
        """
        Saves the collected, non-uniform scraped data to a flat CSV file.
        It first determines the **maximum number of attorneys** found in any single record
        across the entire dataset. County which will have max attorney's count is used to **dynamically generate
        the necessary number of attorney headers** (e.g., 'Attorney Name 1', 'Attorney Name 2', etc.),
        ensuring every record aligns correctly.
        """

        folder_name = "Scraped Data"
        os.makedirs(folder_name, exist_ok=True)
        file_name = "scraped_data.csv"
        file_path = os.path.join(folder_name, file_name)
        max_attorneys = self._get_max_attorneys_in_batch(scraped_data)

        base_headers = ["Case Number", "Case Type", "PDF File"]
        attorney_headers = []
        for i in range(1, max_attorneys + 1):
            attorney_headers.extend([f"Attorney Name {i}", f"Attorney Address {i}"])

        final_headers = base_headers + attorney_headers

        data_rows = []
        required_attorney_cols = len(attorney_headers)

        for item in scraped_data:
            row = [
                item.get("Case Number", ""),
                item.get("Case Type", ""),
                item.get("PDF File", ""),
            ]
            attorney_info: List[Dict[str, str]] = item.get("Attorney Info", [])
            flattened_attorneys = []

            for attorney_dict in attorney_info:
                name = attorney_dict.get("AttorneyName", "")
                address = attorney_dict.get("AttorneyAddress", "")
                flattened_attorneys.extend([name, address])

            padding_needed = required_attorney_cols - len(flattened_attorneys)

            row.extend(flattened_attorneys)
            row.extend([""] * padding_needed)

            data_rows.append(row)

        try:
            with open(file_path, mode="w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)

                writer.writerow(final_headers)
                writer.writerows(data_rows)

            self.logger.info(f"Data successfully saved to {file_path}")
            self.logger.info(f"Total records saved: {len(data_rows)}")

        except Exception as e:
            print(f"Error saving to CSV: {e}")


if __name__ == "__main__":
    scraper = NorthCarolinaScraper()
    scraper.run()
