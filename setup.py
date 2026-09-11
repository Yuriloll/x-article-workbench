from setuptools import find_packages, setup


setup(
    name="x-article-workbench",
    version="1.0.0",
    description="Build an offline, paste-ready X Articles workbench from Markdown.",
    packages=find_packages(),
    include_package_data=True,
    package_data={"x_article_workbench": ["templates/*.html"]},
    python_requires=">=3.9",
    install_requires=["Markdown>=3.5,<4"],
    entry_points={"console_scripts": ["x-article-workbench=x_article_workbench.cli:main"]},
)
