import json
import os
import re
import sys
import time

import boto3
import click
import requests

from botocore.exceptions import ClientError as botocoreClientError
from joblib import Parallel, delayed
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import TimeoutException as SeleniumTimeoutException
from seleniumwire import webdriver

CANONICAL_OWNER = "099720109477"
AWS_UBUNTU_PRO_OWNER_ALIAS = "aws-marketplace"
AWS_UBUNTU_DEEP_LEARNING_OWNER_ALIAS = "amazon"
CANONICAL_MARKETPLACE_PROFILE = "565feec9-3d43-413e-9760-c651546613f2"
AWS_MARKETPLACE_PROFILE = "e6a5002c-6dd0-4d1e-8196-0a1d1857229b"


def get_regions(account_id, username, password, headless):
    # region_dict = {"name": "US East", "location": "N. Virginia", "id": "us-east-1" }
    # return [region_dict]
    # region_dict = {"name": "Asia Pacific", "location": "Seoul", "id": "ap-northeast-2"}
    # return [region_dict]
    # region_dict = {"name": "Europe", "location": "Ireland",
    #                "id": "eu-west-1"}
    # return [region_dict]
    driver_options = Options()
    driver_options.headless = headless
    driver = webdriver.Firefox(options=driver_options)
    wait = webdriver.support.ui.WebDriverWait(driver, 10)
    driver.get("https://{}.signin.aws.amazon.com/console".format(account_id))
    username_element = driver.find_element_by_id("username")
    username_element.send_keys(username)
    password_element = driver.find_element_by_id("password")
    password_element.send_keys(password)
    driver.find_element_by_id("signin_button").click()
    wait.until(lambda driver: driver.find_element_by_name("awsc-mezz-data"))
    region_list_element = driver.find_element_by_name("awsc-mezz-data")
    region_list_str = region_list_element.get_attribute("content")
    region_list = json.loads(region_list_str)["regions"]

    driver.delete_all_cookies()
    driver.close()
    driver.quit()
    return region_list


@click.command()
@click.option(
    "--iam-account-id",
    envvar="IAM_ACCOUNT_ID",
    required=True,
    help="IAM User account ID",
)
@click.option(
    "--iam-username", envvar="IAM_USERNAME", required=True, help="IAM username"
)
@click.option(
    "--iam-password", envvar="IAM_PASSWORD", required=True, help="IAM User account ID"
)
@click.option(
    "--headless/--no-headless",
    default=True,
    help="Use selenium in headless mode to avoid Firefox browser opening",
)
@click.option(
    "--parallel/--no-parallel", default=True, help="Query regions in parallel.",
)
def quicklaunch(iam_account_id, iam_username, iam_password, headless, parallel):
    region_dict_list = get_regions(iam_account_id, iam_username, iam_password, headless)
    driver_options = Options()
    driver_options.headless = headless

    def scrape_quicklaunch_regions(region_dict):
        def get_ami_details(region_client, ami, quickstart_slot, ami_id):
            # Get the ami details
            resp = region_client.describe_images(
                Filters=[{"Name": "image-id", "Values": [ami_id]}],
            )
            resp_len = len(resp.get("Images", []))
            if resp_len:
                image = resp["Images"][0]
                image_owner = image.get("ImageOwnerAlias", image.get("OwnerId"))
                name_regex = None
                if image_owner == CANONICAL_OWNER:
                    image_owner = "Canonical"
                    # This is a Canonical AMI
                    name_regex = (
                        r"ubuntu/images(-(?P<imgtype_path>[\w-]+))?/"
                        r"((?P<virt_storage>\w+(-\w+)?)/)?"
                        r"ubuntu-(?P<suite>\w+)-"
                        r"((?P<release_version>\d\d\.\d\d)-)?"
                        r"((?P<upload_type>\w+)-)?"
                        r"(?P<arch>\w+)-server-"
                        r"(?P<serial>\d+(\.\d{1,2})?)"
                        r"(\-(?P<custom>\w+))?"
                    )
                elif image_owner == AWS_UBUNTU_PRO_OWNER_ALIAS:
                    # This is an AWS Ubuntu AMI - used for Ubuntu Pro listings
                    # trusty-ua-tools-20191128-d984c693-feaa-4be0-bc34-2099410bc9cc-ami-075ab031d5a3404c6.4
                    name_regex = (
                        r".*?"
                        r"(?P<serial>\d+(\.\d{1,2})?)"
                        r"-.*?-"
                        r"(?P<source_ami>ami-\w+).*?"
                    )
                elif image_owner == AWS_UBUNTU_DEEP_LEARNING_OWNER_ALIAS:
                    # This is an AWS Ubuntu AMI - used for
                    # Ubuntu Deep learning and SQL server listings
                    # trusty-ua-tools-20191128-d984c693-feaa-4be0-bc34-2099410bc9cc-ami-075ab031d5a3404c6.4
                    # ubuntu-xenial-16.04-amd64-server-20190212-SQL_2017_Standard-2019.04.02
                    name_regex = (
                        r"ubuntu-(?P<suite>\w+)-"
                        r"((?P<release_version>\d\d\.\d\d)-)?"
                        r"(?P<arch>\w+)-server-"
                        r"(?P<serial>\d+(\.\d{1,2})?)"
                        r"-.*?"
                    )
                if name_regex:
                    ami["quickstart_slot"] = quickstart_slot
                    ami["ami_id"] = ami_id
                    ami["owner"] = image_owner
                    name = image["Name"]
                    match = re.match(name_regex, name)
                    if match:
                        attrs = match.groupdict()
                        for key, value in attrs.items():
                            ami[key] = value
                    return ami
                else:
                    return None
            else:
                return None

        region_identifier = region_dict["id"]
        print("scraping {} ...".format(region_identifier))
        region_session = boto3.Session(region_name=region_identifier)
        region_client = region_session.client("ec2")
        ubuntu_quick_start_listings = []
        driver = webdriver.Firefox(options=driver_options)
        try:
            wait = webdriver.support.ui.WebDriverWait(driver, 20)
            driver.get(
                "https://{}.signin.aws.amazon.com/console".format(iam_account_id)
            )
            username_element = driver.find_element_by_id("username")
            username_element.send_keys(iam_username)
            password_element = driver.find_element_by_id("password")
            password_element.send_keys(iam_password)
            driver.find_element_by_id("signin_button").click()

            wait.until(lambda driver: driver.find_element_by_id("nav-regionMenu"))
            driver.find_element_by_id("nav-regionMenu").click()
            # Are we on the correct region already?
            region_full_name = "{} ({}){}".format(
                region_dict["name"], region_dict["location"], region_identifier
            )
            current_region_element = driver.find_element_by_class_name("current-region")
            if current_region_element.text != region_full_name:
                # wait.until(
                #     lambda driver: driver.find_element_by_xpath(
                #         '//a[@data-region-id="{}"]'.format(region_identifier)
                #     )
                # )
                # driver.find_element_by_xpath(
                #     '//a[@data-region-id="{}"]'.format(region_identifier)
                # ).click()
                # Navigating from an opt-in region to another opt in region
                # leads to http 500 errors. As such we can load the
                # console for that region directly.
                driver.get(
                    "https://{}.console.aws.amazon.com/ec2/home?region={}#Home:".format(
                        region_identifier, region_identifier
                    )
                )
            else:
                driver.find_element_by_id("nav-regionMenu").click()

            wait.until(lambda driver: driver.find_element_by_id("nav-servicesMenu"))
            driver.find_element_by_id("nav-servicesMenu").click()
            wait.until(
                lambda driver: driver.find_element_by_xpath(
                    '//li[@data-service-id="ec2"]'
                )
            )
            driver.find_element_by_xpath('//li[@data-service-id="ec2"]/a').click()
            wait.until(
                lambda driver: driver.find_element_by_xpath(
                    '//iframe[contains(@id, "-react-frame")]'
                )
            )
            dashboard_iframe = driver.find_element_by_xpath(
                '//iframe[contains(@id, "-react-frame")]'
            )
            driver.switch_to.frame(dashboard_iframe)
            wait.until(
                lambda driver: driver.find_element_by_class_name(
                    "awsui-button-dropdown-container"
                )
            )
            print("{} - Opening launch instance page".format(region_identifier))
            driver.find_element_by_class_name("awsui-button-dropdown-container").click()
            wait.until(
                lambda driver: driver.find_element_by_xpath(
                    "//*[contains(@href, '#LaunchInstanceWizard:')]"
                )
            )
            driver.find_element_by_xpath(
                "//*[contains(@href, '#LaunchInstanceWizard:')]"
            ).click()
            driver.switch_to.default_content()
            wait.until(
                lambda driver: driver.find_element_by_id(
                    "gwt-debug-tab-QUICKSTART_AMIS"
                )
            )
            driver.find_element_by_id("gwt-debug-tab-QUICKSTART_AMIS").click()
            wait.until(
                lambda driver: driver.find_element_by_id(
                    "gwt-debug-tab-QUICKSTART_AMIS"
                )
            )
            wait.until(
                lambda driver: driver.find_element_by_id("gwt-debug-paginatorLabel")
            )
            # wait until JSON request is complete loads.
            # 3 seconds seems to be enough for all regions
            print("{} - Querying quickstart list".format(region_identifier))
            time.sleep(3)
            for request in list(driver.requests):
                if "call=getQuickstartList" in request.path and request.response:
                    region_quickstart_entries = json.loads(request.response.body)
                    with open(
                        "{}-getQuickstartList.json".format(region_identifier), "w"
                    ) as outfile:
                        json.dump(region_quickstart_entries, outfile, indent=4)

                    quickstart_slot = 0
                    for ami in region_quickstart_entries["amiList"]:
                        quickstart_slot = quickstart_slot + 1
                        if ami["platform"] == "ubuntu":
                            if ami.get("imageId64", None):
                                print(
                                    "{} - Querying ami details for AMD64 AMI {}".format(
                                        region_identifier, ami.get("imageId64")
                                    )
                                )
                                canonical_amd64_ami = get_ami_details(
                                    region_client,
                                    ami.copy(),
                                    quickstart_slot,
                                    ami.get("imageId64"),
                                )
                                if canonical_amd64_ami:
                                    canonical_amd64_ami["listing_arch"] = "amd64"
                                    ubuntu_quick_start_listings.append(
                                        canonical_amd64_ami
                                    )

                            if ami.get("imageIdArm64", None):
                                print(
                                    "{} - Querying ami details for ARM64 AMI {}".format(
                                        region_identifier, ami.get("imageIdArm64")
                                    )
                                )
                                canonical_arm64_ami = get_ami_details(
                                    region_client,
                                    ami.copy(),
                                    quickstart_slot,
                                    ami.get("imageIdArm64"),
                                )
                                if canonical_arm64_ami:
                                    canonical_arm64_ami["listing_arch"] = "arm64"
                                    ubuntu_quick_start_listings.append(
                                        canonical_arm64_ami
                                    )

                    # We only need one list so we can break here
                    break
        except SeleniumTimeoutException as ste:
            print(
                "SeleniumTimeoutException encountered when querying region {} ".format(
                    region_identifier
                )
            )
            print(ste.msg)
        except botocoreClientError as bce:
            print(
                "botocoreClientError encountered when AMI for region {} ".format(
                    region_identifier
                )
            )
            print(bce)
        finally:
            driver.delete_all_cookies()
            driver.close()
            driver.quit()
        return (region_identifier, ubuntu_quick_start_listings)

    n_jobs = -1 if parallel else 1
    parallel_quickstart_entries = Parallel(n_jobs=n_jobs)(
        delayed(scrape_quicklaunch_regions)(region_dict)
        for region_dict in region_dict_list
    )

    sorted_parallel_quickstart_entries = sorted(
        parallel_quickstart_entries, key=lambda tup: tup[0]
    )

    with open("quickstart_entries.json", "w") as quickstart_entries_json:
        json.dump(sorted_parallel_quickstart_entries, quickstart_entries_json, indent=4)

    issues = {}

    for region, ubuntu_quickstart_entries in sorted_parallel_quickstart_entries:
        print(region)
        region_amis = []
        region_expected_listings = {
            "16.04": ["amd64", "arm64"],
            "18.04": ["amd64", "arm64"],
            "20.04": ["amd64", "arm64"],
        }
        for ubuntu_quickstart_entry in ubuntu_quickstart_entries:
            print(
                "{} {}\n\t{} {} {} {} {} \n\t\t(Slot: {} , Description: {})".format(
                    ubuntu_quickstart_entry.get("title", ""),
                    ubuntu_quickstart_entry.get("listing_arch", ""),
                    ubuntu_quickstart_entry.get("release_version", ""),
                    ubuntu_quickstart_entry.get("serial", ""),
                    ubuntu_quickstart_entry.get("arch", ""),
                    ubuntu_quickstart_entry.get("ami_id", ""),
                    ubuntu_quickstart_entry.get("owner", ""),
                    ubuntu_quickstart_entry.get("quickstart_slot", ""),
                    ubuntu_quickstart_entry.get("description", ""),
                )
            )
            if ubuntu_quickstart_entry.get("owner", "") == "Canonical":
                release_version = ubuntu_quickstart_entry.get("release_version", "")
                if (
                    ubuntu_quickstart_entry.get("arch", "")
                    in region_expected_listings[release_version]
                ):
                    region_expected_listings[release_version].remove(
                        ubuntu_quickstart_entry.get("arch", "")
                    )
                region_amis.append(ubuntu_quickstart_entry.get("ami_id", ""))

            if ubuntu_quickstart_entry.get(
                "owner", ""
            ) == "Canonical" and ubuntu_quickstart_entry.get(
                "arch", ""
            ) != ubuntu_quickstart_entry.get(
                "listing_arch", ""
            ):
                issues.setdefault(region, []).append(
                    "'{}' listing arch {} and AMI ({}) arch {} are not equal ".format(
                        ubuntu_quickstart_entry.get("title", ""),
                        ubuntu_quickstart_entry.get("listing_arch", ""),
                        ubuntu_quickstart_entry.get("ami_id", ""),
                        ubuntu_quickstart_entry.get("arch", ""),
                    )
                )
            if (
                ubuntu_quickstart_entry.get("owner", "") == "Canonical"
                and int(ubuntu_quickstart_entry.get("quickstart_slot", "")) > 10
            ):
                issues.setdefault(region, []).append(
                    "'{}' {} listing slot is greater than 10 - slot {}".format(
                        ubuntu_quickstart_entry.get("title", ""),
                        ubuntu_quickstart_entry.get("listing_arch", ""),
                        ubuntu_quickstart_entry.get("quickstart_slot", ""),
                    )
                )
            if (
                ubuntu_quickstart_entry.get("owner", "") == "Canonical"
                and region_amis.count(ubuntu_quickstart_entry.get("ami_id", "")) > 1
            ):
                issues.setdefault(region, []).append(
                    "'{}' {} listing AMI {} appears more than once  ".format(
                        ubuntu_quickstart_entry.get("title", ""),
                        ubuntu_quickstart_entry.get("listing_arch", ""),
                        ubuntu_quickstart_entry.get("ami_id", ""),
                    )
                )
        print()

        for release_version, arches in region_expected_listings.items():
            if len(arches) > 0:
                for arch in arches:
                    issues.setdefault(region, []).append(
                        "There are no listings for {} {}  ".format(
                            release_version, arch,
                        )
                    )
    for region, region_issues in issues.items():
        print(region)
        for region_issue in region_issues:
            print("\t* {}".format(region_issue))
        print()


@click.command()
def marketplace():
    marketplace_profiles = [CANONICAL_MARKETPLACE_PROFILE, AWS_MARKETPLACE_PROFILE]
    public_profile_url_base = "https://aws.amazon.com/marketplace/seller-profile"
    marketplace_listings = []
    marketplace_listings_filename = "marketplace-listings.json"
    if not os.path.isfile(marketplace_listings_filename):
        try:
            driver_options = Options()
            driver_options.headless = False
            driver = webdriver.Firefox(options=driver_options)
            wait = webdriver.support.ui.WebDriverWait(driver, 30)
            for marketplace_profile in marketplace_profiles:
                public_profile_url = "{}?id={}".format(
                    public_profile_url_base, marketplace_profile
                )

                driver.get(public_profile_url)
                wait.until(
                    lambda driver: driver.find_element_by_class_name("awsui-table-pagination-next-page")
                )
                next_page = True
                page = 1
                while next_page:
                    if page !=1:
                        driver.find_element_by_class_name("awsui-table-pagination-next-page").click()
                        wait.until(
                            lambda driver: driver.find_element_by_xpath(
                                '//awsui-table[@data-test-selector="searchResultsTable"]'
                            )
                        )
                    for request in list(driver.requests):
                        if "marketplace/api/awsmpdiscovery" in request.path and request.response:
                            seller_marketplace_entries = json.loads(request.response.body)
                            marketplace_listings.extend(seller_marketplace_entries.get('ListingSummaries'))
                            next_page_token = seller_marketplace_entries.get('NextToken', None)
                            if not next_page_token:
                                next_page = False
                            print(seller_marketplace_entries)
                            # We only need one list so we can break here
                            del driver.requests
                            break
                    page = page + 1
        finally:
            driver.delete_all_cookies()
            driver.close()
            driver.quit()

        print(marketplace_listings)
        with open(
                marketplace_listings_filename, "w"
        ) as outfile:
            json.dump(marketplace_listings, outfile, indent=4)
    else:
        with open(marketplace_listings_filename, "r") as marketplace_listings_file:
            marketplace_listings = json.load(marketplace_listings_file)

    products = []
    for marketplace_listing in marketplace_listings:

        product_title = marketplace_listing.get('DisplayAttributes').get('Title')

        product_creator_id = marketplace_listing.get('ProductAttributes').get('Creator').get('Value')
        product_creator_title = marketplace_listing.get('ProductAttributes').get('Creator').get('DisplayName')
        # if this is a listing on the AWS seller profile then we only want to scrape the Ubuntu listings
        if product_creator_id == AWS_MARKETPLACE_PROFILE:
            if "Ubuntu" not in product_title:
                continue

        product_version = marketplace_listing.get('DisplayAttributes').get('VersionInformation', {}).get('RecommendedVersion', '')

        product_description = marketplace_listing.get('DisplayAttributes').get('LongDescription')
        product_type = marketplace_listing.get('FulfillmentOptionTypes')[0].get('DisplayName')
        product_id = marketplace_listing.get('Id')
        marketplace_url = "https://aws.amazon.com/marketplace/pp/{}".format(product_id)

        release_version = ""
        serial = ""
        version_regex = (
            r".*?(?P<release_version>\d\d\.\d\d?)"
            r".*?(?P<serial>\d\d\d\d\d\d\d\d(\.\d{1,2})?).*?"
        )

        match = re.match(version_regex, product_version)

        if match:
            attrs = match.groupdict()
            release_version = attrs.get("release_version", None)
            serial = attrs.get("serial", None)

        product_unique_identifier = "{} ({}) - {}".format(
            product_title, product_type, serial
        )
        product = {
            "unique_identifier": product_unique_identifier,
            "creator": product_creator_title,
            "version": product_version,
            "release_version": release_version,
            "title": product_title,
            "description": product_description,
            "serial": serial,
            "marketplace_url": marketplace_url,
            "type": product_type,
        }
        products.append(product)

    for product in products:
        print(
            "\n{}\n\t\t"
            "Creator: {}\n\t\t"
            "Release: {}\n\t\t"
            "Serial: {}\n\t\t"
            "Version: {}\n\t\t"
            "Type: {}\n\t\t"
            "Title: {}\n\t\t"
            "Description: \n\t\t\t\t{}\n\t\t"
            "URL: {}\n\t\t".format(
                product["unique_identifier"],
                product["creator"],
                product["release_version"],
                product["serial"],
                product["version"],
                product["type"],
                product["title"],
                product["description"].replace("\n", "\n\t\t\t\t"),
                product["marketplace_url"],
            )
        )


@click.group()
def main():
    pass


main.add_command(quicklaunch)
main.add_command(marketplace)

if __name__ == "__main__":
    sys.exit(main())
