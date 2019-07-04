# AWS Marketplace Ubuntu AMI Scraper

CLI to return the Ubuntu AMIs in AWS marketplace

## Basic setup

Install the requirements:
```
$ pip install -r requirements.txt
```

You will also need [Firefox](https://www.mozilla.org/en-US/firefox/new/) installed and [geckodriver](https://github.com/mozilla/geckodriver/releases) available in your PATH.

I recommend you create a new IAM user with only policy "AWSMarketplaceRead-only" granted.

You will also need to set up your aws credentials for use with [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/configuration.html)

Run the application:
```
$ python -m aws_marketplace_ubuntu_scraper --help

$ python -m aws_marketplace_ubuntu_scraper --iam-account-id="YOUR IAM ACCOUNT ID" --iam-username="YOUR IAM USERNAME" --iam-password="YOUR IAM PASSSWORD"
```

To run the tests:
```
    $ pytest
```
