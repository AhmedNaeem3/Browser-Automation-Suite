import csv
import os
import sys
from datetime import datetime
import time
from bs4 import BeautifulSoup
from logger import setup_logging
from dateutil.relativedelta import relativedelta
from playwright.sync_api import sync_playwright, Page


class NewyorkCourtScraper:

    def __init__(self):
        self.base_url = "https://websurrogates.nycourts.gov"
        self.search_url = "https://websurrogates.nycourts.gov/File/FileSearch"
        self.logger = setup_logging()
        self.cdp_url = os.getenv("CDP_URL")
        self.max_navigation_retries = 3
        self.navigation_delay_seconds = 5

    def connect_and_setup(self, p: sync_playwright) -> Page:
        """Connects to an already running Chrome instance via CDP and opens a new tab."""
        self.logger.info("Attempting to connect to running Chrome instance.")
        browser = p.chromium.connect_over_cdp(self.cdp_url)
        context = browser.contexts[0]
        page = context.new_page()
        self.logger.info(
            f"Successfully connected and created new tab. Navigating to {self.search_url}"
        )
        return page

    def get_attorney_info(
        self, page: Page, scraped_data: list, county_name: str, month_name: str
    ):
        """
        Extracts attorney details from the current page of search results.

        This function iterates through all file links on the results table, navigates
        to the detailed file history page and then scrapes required data fields.
        """

        table_selector = "#NameResultsTable"
        link_selector = f"{table_selector} .ButtonAsLink"

        try:
            page.wait_for_selector(table_selector, timeout=90000)
        except Exception:
            self.logger.warning(
                "File Search results table not found. Cannot proceed with scraping details."
            )
            return

        link_locators = page.locator(link_selector)
        try:
            link_locators.first.wait_for(state="visible", timeout=90000)

            link_count = link_locators.count()

            self.logger.info(f"Found {link_count} file result links to process.")
        except TimeoutError as e:
            self.logger.error(
                f"Timed out waiting for file result links: {e} (Selector: {link_selector}). Skipping this result set."
            )
            return

        for i in range(link_count):
            current_link = page.locator(link_selector).nth(i)
            current_link.wait_for(state="visible", timeout=60000)
            file_number = current_link.text_content()
            if not file_number:
                self.logger.warning(
                    f"File Link not found for index {i + 1} of county: {county_name} and month {month_name}. Skipping this file."
                )
                continue
            self.logger.info(f"Clicking link for File #: {file_number.strip()}")

            try:
                with page.expect_navigation():
                    current_link.click(timeout=90000)
            except Exception as e:
                self.logger.error(
                    f"Failed to click/navigate for File #: {file_number.strip()} of county: {county_name} and month is: {month_name}. Skipping. Error: {e}"
                )
                continue

            file_history_page = page.content()
            soup = BeautifulSoup(file_history_page, "html.parser")
            proceeding_type = None
            estate_attorney = None
            estate_attorney_firm = None
            file_no = None

            try:
                county = soup.select_one("#Court").get("value")
                file_no = soup.select_one("#FileNumber").get("value")

                proceeding_label = soup.select_one('text:-soup-contains("Proceeding:")')
                if proceeding_label:
                    parent_tag = proceeding_label.find_parent("div", class_="col-sm-8")
                    if parent_tag:
                        value_element = parent_tag.select_one("div.col-sm-9 span text")
                        if value_element:
                            proceeding_type = value_element.text.strip()

                attorney_element = soup.select_one(
                    'text:-soup-contains("Estate Attorney:") > .BoldFont'
                )
                if attorney_element:
                    estate_attorney = attorney_element.text.strip()

                attorney_firm = soup.select_one(
                    'text:-soup-contains("Estate Attorney Firm:") > text[style*="font-weight:bold"]'
                )
                if attorney_firm:
                    estate_attorney_firm = attorney_firm.text.strip()

            except Exception as e:
                self.logger.error(
                    f"Data extraction failed for File #: {file_no or file_number.strip()} of county: {county_name} and month: {month_name}. Error: {e}"
                )
                continue

            attorney_info = {
                "County": county,
                "File Number": file_no,
                "Proceeding Type": proceeding_type,
                "Estate Attorney": estate_attorney,
                "Estate Attorney Firm": estate_attorney_firm,
            }

            self.logger.info(
                f"Successfully Scraped Attorney Info for file #: {file_no}"
            )
            scraped_data.append(attorney_info)

            self.logger.info("Navigating back to the file search result page...")
            page.go_back(timeout=90000)

        self.logger.info(
            "Checking pagination to see if there are additional pages to process"
        )
        self.get_next_page(page, scraped_data, county_name, month_name)

    def get_next_page(
        self, page: Page, scraped_data: list, county_name: str, month_name: str
    ):
        """
        Handles pagination for a given month's search results.

        This function checks for the presence of subsequent result pages, iterates through
        each non-active page link, clicks and waits for navigation, delegates the scraping
        to `self.get_attorney_info()`, and uses `page.go_back()` to reset the browser state
        for the next iteration.
        """

        link_selector = "ul.pagination a.page-link:not(li.active a.page-link)"
        pagination_locators = page.locator(link_selector)

        self.logger.info(
            "Checking pagination to see if there are additional pages to process"
        )
        if pagination_locators.first.is_visible(timeout=60000):
            total_pages_to_scrape = pagination_locators.count()

            self.logger.info(
                f"Found {total_pages_to_scrape} subsequent pages for county: {county_name} and month: {month_name}."
            )
            for i in range(total_pages_to_scrape):
                next_page_link = page.locator(link_selector).nth(i)
                next_page_link.wait_for(state="visible", timeout=30000)
                next_page_endpoint = next_page_link.get_attribute("href")
                next_page_url = f"{self.base_url}{next_page_endpoint}"
                self.logger.info(
                    f"Navigating to page {next_page_url.strip()} for county: {county_name} and month: {month_name}."
                )

                try:
                    with page.expect_navigation(wait_until="domcontentloaded"):
                        next_page_link.click(timeout=30000)
                except Exception as e:
                    self.logger.error(
                        f"Failed to navigate to page {next_page_url.strip()} for county: {county_name} and month: {month_name}. Skipping. Error: {e}"
                    )
                    continue

                self.get_attorney_info(page, scraped_data, county_name, month_name)
                page.go_back(timeout=90000)

        else:

            self.logger.info(
                f"Next pages not found for the county: {county_name} and month: {month_name}"
            )
            return

    def generate_monthly_ranges(self, year):
        """
        Generates a list of precise start and end dates for all 12 months of the specified year.
        """
        monthly_ranges = []

        start_date = datetime(year, 1, 1)

        for month in range(12):
            current_month_start = start_date + relativedelta(months=month)
            current_month_end = start_date + relativedelta(months=month + 1, days=-1)

            monthly_ranges.append(
                {
                    "month_name": current_month_start.strftime("%B"),
                    "start_date": current_month_start.strftime("%m/%d/%Y"),
                    "end_date": current_month_end.strftime("%m/%d/%Y"),
                }
            )

        return monthly_ranges

    def run(self):
        """
        Orchestrates the end-to-end data extraction from the New York State Unified Court System Records search form.

        This function executes the complete scraping cycle by systematically iterating through
        and applying complex filters: Proceeding Type, County, and granular monthly date ranges (2024).
        It manages state across these loops, applies resilient navigation retry logic, handles
        empty search results, processes multi-page pagination, and persists the final scraped
        data to CSV, ensuring robust and complete data capture.
        """
        self.logger.info("Starting the New York Court script...")

        try:
            with sync_playwright() as p:
                page = self.connect_and_setup(p)

                target_year = 2024
                monthly_ranges = self.generate_monthly_ranges(target_year)
                page.goto(self.search_url, timeout=60000, wait_until="domcontentloaded")
                response = page.content()
                soup = BeautifulSoup(response, "html.parser")

                all_countys = soup.select("#CourtSelect option")[1:]

                count_proceeding_types = 0
                while True:
                    proceeding_type_input = "PROBATE & PRELIMINARY PETITIONS"
                    if count_proceeding_types == 1:
                        proceeding_type_input = "ADMINISTRATION PETITION"
                    if count_proceeding_types > 1:
                        self.logger.info(
                            "Scraped complete data for both proceeding types PROBATE & PRELIMINARY PETITIONS and ADMINISTRATION PETITIONS."
                        )
                        break
                    count_proceeding_types += 1
                    self.logger.info(
                        f"Start scraping for proceeding type: {proceeding_type_input}"
                    )

                    for county in all_countys:
                        county_value = county.get("value")
                        county_name = county.get_text(strip=True)
                        scraped_data = []
                        for range_info in monthly_ranges:

                            for attempt in range(self.max_navigation_retries):
                                try:
                                    self.logger.info(
                                        f"Navigation Attempt {attempt + 1}/{self.max_navigation_retries} to {self.search_url}"
                                    )
                                    page.goto(
                                        self.search_url,
                                        timeout=60000,
                                        wait_until="domcontentloaded",
                                    )
                                    self.logger.info(
                                        f"Navigated to search page: {page.url}"
                                    )
                                    break

                                except Exception as e:
                                    if attempt < self.max_navigation_retries - 1:
                                        self.logger.warning(
                                            f"Navigation failed ({e.__class__.__name__}). Retrying in {self.navigation_delay_seconds}s..."
                                        )
                                        time.sleep(self.navigation_delay_seconds)
                                    else:
                                        self.logger.critical(
                                            f"FATAL ERROR: Navigation failed after {self.max_navigation_retries} attempts. Terminating script."
                                        )
                                        raise e

                            self.logger.info(
                                f"Attempting to fill the search form with county: {county_name} and proceeding type: {proceeding_type_input} and month: {range_info['month_name']}"
                            )
                            page.select_option("#CourtSelect", value=county_value)
                            page.select_option(
                                "#SelectedProceeding",
                                value=proceeding_type_input,
                            )

                            page.type(
                                "#txtFilingDateFrom",
                                range_info["start_date"],
                                delay=100,
                            )
                            page.type(
                                "#txtFilingDateTo", range_info["end_date"], delay=100
                            )

                            with page.expect_navigation():
                                self.logger.info("Clicking on the search button")
                                page.locator("#FileSearchSubmit2").click()

                            no_results_found = (
                                page.locator(".validation-summary-errors")
                                .filter(has_text="No Matching Files Were Found")
                                .count()
                                > 0
                            )

                            if no_results_found:
                                self.logger.info(
                                    f"No results found for county: {county_name} for month {range_info['month_name']} and proceeding type: {proceeding_type_input}."
                                )
                                continue

                            self.logger.info(
                                f"Navigated to file search results page: {page.url}"
                            )
                            self.get_attorney_info(
                                page,
                                scraped_data,
                                county_name,
                                range_info["month_name"],
                            )

                            self.logger.info(
                                f"Completed processing for {range_info['month_name']} records of county: {county_name}."
                            )

                        if scraped_data:
                            self.logger.info(
                                f"Successfully Scraped data of county: {county_name} and its proceeding type: {proceeding_type_input}. For all months, Now Saving Data to CSV"
                            )
                            self.save_to_csv(scraped_data)
                        else:
                            self.logger.info(
                                f"No data scraped for county: {county_name} and proceeding type: {proceeding_type_input}. Because no pagination links were found."
                            )

                page.close()
                self.logger.info(
                    "Tab closed. Script finished successfully. Scraped all records."
                )

        except Exception as e:
            self.logger.error(f"\nFATAL ERROR during execution: {e}")

            sys.exit(1)

    def save_to_csv(self, scraped_data):
        """Saves the scraped data to a CSV file."""
        file_exists = os.path.exists("scraped_data.csv")

        try:
            with open(
                "scraped_data.csv", mode="a", newline="", encoding="utf-8"
            ) as csvfile:
                writer = csv.writer(csvfile)

                if not file_exists:
                    writer.writerow(
                        [
                            "County",
                            "File Number",
                            "Proceeding Type",
                            "Estate Attorney",
                            "Estate Attorney Firm",
                        ]
                    )

                for item in scraped_data:
                    writer.writerow(
                        [
                            item.get("County", ""),
                            item.get("File Number", ""),
                            item.get("Proceeding Type", ""),
                            item.get("Estate Attorney", ""),
                            item.get("Estate Attorney Firm", ""),
                        ]
                    )

            self.logger.info("Data successfully saved to scraped_data.csv")

        except Exception as e:
            self.logger.error(f"Error saving to CSV: {e}")


if __name__ == "__main__":
    scraper = NewyorkCourtScraper()
    scraper.run()
