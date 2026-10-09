"""CMDB client. **The only implementation is a mock** — it returns hard-coded fixtures.

To wire in the real ServiceNow CMDB, replace the internals of `MockCmdbClient` with an
HTTP client against the actual CMDB endpoint. The `CmdbClient` interface should not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class CmdbApplication:
    """APM Application — a portfolio entity that groups one or more CIs.

    App IDs are sourced from the Pearson APM data (SVC0xxxx series).
    Currently fixture data; a real ServiceNow APM API call would replace it.
    """

    app_id: str           # e.g. "SVC00595"
    name: str             # e.g. "PLA Learning Analytics- Performance XL"
    owner: str            # IT Application Owner name
    business_unit: str    # e.g. "Digital & Technology"
    lifecycle_stage: str  # "Operational" | "End of Life"
    lifecycle_status: str # "In Use" | "Retired" | "Obsolete"


@dataclass(slots=True, frozen=True)
class CmdbCi:
    """Configuration Item — a service registered in CMDB."""

    ci_id: str  # e.g. "CI0093421"
    name: str  # e.g. "catalog-svc"
    domain: str  # e.g. "commerce"
    team_id: str  # e.g. "TM-COMMERCE-PLATFORM"
    team_email: str
    repo: str | None = None  # GitHub org/repo
    urn: str = ""  # canonical URN
    produces: list[str] = field(default_factory=list)  # subject names
    consumes: list[str] = field(default_factory=list)  # subject names
    active: bool = True
    app_id: str | None = None  # parent APM Application (SVC0xxxx)


# Mock APM application data sourced from data/cmdb-apm-data.xlsx.
# NOTE: that spreadsheet lives in the source repo and was not migrated.
# Eight real Pearson App IDs covering the service domains in _FIXTURES below.
_APP_FIXTURES: dict[str, CmdbApplication] = {
    "SVC00595": CmdbApplication(
        app_id="SVC00595",
        name="PLA Learning Analytics- Performance XL",
        owner="George Amaya",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC04474": CmdbApplication(
        app_id="SVC04474",
        name="PAICE Assessments and Interactives",
        owner="Patrick Kanouse",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC00306": CmdbApplication(
        app_id="SVC00306",
        name="eCommerce HA and Spain",
        owner="Omar Cruz",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC01909": CmdbApplication(
        app_id="SVC01909",
        name="DAC Product Data Lake",
        owner="Doug Thomas",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC02149": CmdbApplication(
        app_id="SVC02149",
        name="PAICE - ContentAPIs",
        owner="Dave Gotlieb",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC02645": CmdbApplication(
        app_id="SVC02645",
        name="Finance - Sage XRT Spain",
        owner="Miguel Angel Arranz Gomez",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC00925": CmdbApplication(
        app_id="SVC00925",
        name="Identity Enabling Services (IES/PI)",
        owner="Lavanya Devi Devadass",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC01737": CmdbApplication(
        app_id="SVC01737",
        name="EDW Enterprise Data Warehouse & Analytics Platform",
        owner="Dileep Mantena",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC01328": CmdbApplication(
        app_id="SVC01328",
        name="GIP - Global Integration Platform",
        owner="Rajnivas Karunakaran",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC04622": CmdbApplication(
        app_id="SVC04622",
        name="BigCommerce",
        owner="Alex Clark",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC00491": CmdbApplication(
        app_id="SVC00491",
        name="Salesforce Global Sales and Service (One CRM)",
        owner="Jeevan Bussari",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC01770": CmdbApplication(
        app_id="SVC01770",
        name="Oracle E-Business Suite (TEP)",
        owner="Dinesh Narayan",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
    "SVC01745": CmdbApplication(
        app_id="SVC01745",
        name="Oracle HCM Fusion",
        owner="Justin Carpenter",
        business_unit="Digital & Technology",
        lifecycle_stage="Operational",
        lifecycle_status="In Use",
    ),
}

# Hard-coded fixture data. Replace with real CMDB calls to go live.
_FIXTURES: dict[str, CmdbCi] = {
    "CI0093421": CmdbCi(
        ci_id="CI0093421",
        name="catalog-svc",
        domain="product",
        team_id="TM-PRODUCT-CATALOG",
        team_email="product-catalog@pearson.com",
        repo="pearson/catalog-svc",
        urn="urn:pearson:service:product:catalog-svc",
        # The ten coordinates this service really produces, ADDED to the demo
        # subjects rather than replacing them.
        #
        # Why both. Left with only the demo subjects, product was the one
        # domain whose service showed no produces in the portfolio while
        # price, program and stock -- added later with real coordinates --
        # showed theirs, and find_producers_of could not answer "who produces
        # product.product.gold" for the only domain that had registered it.
        #
        # Why the demo subjects stay. The portfolio FILTERS this manifest
        # against the artifacts actually registered in Apicurio, so an entry
        # for something unregistered never reaches the UI -- the seven below
        # are inert in every real environment. They are not inert in
        # qe/tests/test_portfolio.py, which builds its whole scenario on three
        # of them: catalog-svc produces product.product.created-value, which
        # enrollment-svc consumes (the downstream edge), and consumes
        # content.asset.published-value, which content-svc produces (the
        # upstream edge). Dropping them silently deleted that scenario.
        produces=[
            # Registered 2026-09-25 -- the real coordinates.
            "product.product.mapping",
            "product.product.gold",
            "product.product.api-created",
            "product.product.api-updated",
            "product.product.event-created",
            "product.product.event-updated",
            "product.product.message-created",
            "product.product.message-updated",
            "product.marketing-copy.gold",
            "product.marketing-copy.mapping",
            # Demo subjects: unregistered, so filtered out of the portfolio;
            # retained because test_portfolio.py's fixture depends on them.
            "product.product-value",
            "product.product.created-value",
            "product.product.updated-value",
            "product.product.deprecated-value",
            "product.sku.added-value",
            "product.bundle.published-value",
            "product.catalog-value",
        ],
        consumes=[
            "content.asset.published-value",
            "finance.pricing.changed-value",
            "customer.preference.updated-value",
        ],
        app_id="SVC01328",
    ),
    "CI0093422": CmdbCi(
        ci_id="CI0093422",
        name="enrollment-svc",
        domain="learning",
        team_id="TM-LEARNING-PLATFORM",
        team_email="learning-platform@pearson.com",
        repo="pearson/enrollment-svc",
        urn="urn:pearson:service:learning:enrollment-svc",
        produces=["learning.learner.enrolled-value", "learning.progress.updated-value"],
        consumes=["product.product.created-value", "identity.user.provisioned-value"],
        app_id="SVC04622",
    ),
    "CI0093423": CmdbCi(
        ci_id="CI0093423",
        name="checkout-svc",
        domain="commerce",
        team_id="TM-COMMERCE-PLATFORM",
        team_email="commerce-platform@pearson.com",
        repo="pearson/checkout-svc",
        urn="urn:pearson:service:commerce:checkout-svc",
        produces=["commerce.order.placed-value"],
        consumes=["product.product.created-value", "customer.customer.updated-value"],
        app_id="SVC04622",
    ),
    "CI0093430": CmdbCi(
        ci_id="CI0093430",
        name="content-svc",
        domain="content",
        team_id="TM-CONTENT-PLATFORM",
        team_email="content-platform@pearson.com",
        repo="pearson/content-svc",
        urn="urn:pearson:service:content:content-svc",
        produces=["content.asset.published-value"],
        consumes=[],
        app_id="SVC00491",
    ),
    "CI0093431": CmdbCi(
        ci_id="CI0093431",
        name="pricing-svc",
        domain="finance",
        team_id="TM-FINANCE-PRICING",
        team_email="finance-pricing@pearson.com",
        repo="pearson/pricing-svc",
        urn="urn:pearson:service:finance:pricing-svc",
        produces=["finance.pricing.changed-value"],
        consumes=["product.product-value"],
        app_id="SVC02645",
    ),
    "CI0093432": CmdbCi(
        ci_id="CI0093432",
        name="customer-prefs-svc",
        domain="customer",
        team_id="TM-CUSTOMER-PLATFORM",
        team_email="customer-platform@pearson.com",
        repo="pearson/customer-prefs-svc",
        urn="urn:pearson:service:customer:customer-prefs-svc",
        produces=["customer.preference.updated-value"],
        consumes=[],
        app_id="SVC00491",
    ),
    # >>> seeded by seed_services.py — do not edit by hand
    "CI0093440": CmdbCi(
        ci_id="CI0093440",
        name="course-svc",
        domain="learning",
        team_id="TM-LEARNING-PLATFORM",
        team_email="learning-platform@pearson.com",
        repo="pearson/course-svc",
        urn="urn:pearson:service:learning:course-svc",
        produces=[],
        consumes=[
            "product.product.created-value",
            "product.bundle.published-value",
            "content.asset.published-value",
        ],
        app_id="SVC00595",
    ),
    "CI0093441": CmdbCi(
        ci_id="CI0093441",
        name="progress-svc",
        domain="learning",
        team_id="TM-LEARNING-PLATFORM",
        team_email="learning-platform@pearson.com",
        repo="pearson/progress-svc",
        urn="urn:pearson:service:learning:progress-svc",
        produces=["learning.progress.updated-value"],
        consumes=["learning.learner.enrolled-value"],
        app_id="SVC00595",
    ),
    "CI0093442": CmdbCi(
        ci_id="CI0093442",
        name="assessment-svc",
        domain="assessment",
        team_id="TM-ASSESSMENT-PLATFORM",
        team_email="assessment-platform@pearson.com",
        repo="pearson/assessment-svc",
        urn="urn:pearson:service:assessment:assessment-svc",
        produces=[],
        consumes=[
            "learning.learner.enrolled-value",
            "learning.progress.updated-value",
        ],
        app_id="SVC04474",
    ),
    "CI0093443": CmdbCi(
        ci_id="CI0093443",
        name="content-svc",
        domain="content",
        team_id="TM-CONTENT-PLATFORM",
        team_email="content-platform@pearson.com",
        repo="pearson/content-svc",
        urn="urn:pearson:service:content:content-svc",
        produces=["content.asset.published-value"],
        consumes=[],
        app_id="SVC00491",
    ),
    "CI0093444": CmdbCi(
        ci_id="CI0093444",
        name="credentials-svc",
        domain="credentials",
        team_id="TM-CREDENTIALS-PLATFORM",
        team_email="credentials-platform@pearson.com",
        repo="pearson/credentials-svc",
        urn="urn:pearson:service:credentials:credentials-svc",
        produces=[],
        consumes=[
            "learning.progress.updated-value",
            "commerce.order.placed-value",
        ],
        app_id="SVC00595",
    ),
    "CI0093445": CmdbCi(
        ci_id="CI0093445",
        name="identity-svc",
        domain="identity",
        team_id="TM-IDENTITY-PLATFORM",
        team_email="identity-platform@pearson.com",
        repo="pearson/identity-svc",
        urn="urn:pearson:service:identity:identity-svc",
        produces=[],
        consumes=["customer.customer.created-value"],
        app_id="SVC00925",
    ),
    "CI0093446": CmdbCi(
        ci_id="CI0093446",
        name="subscription-svc",
        domain="commerce",
        team_id="TM-COMMERCE-PLATFORM",
        team_email="commerce-platform@pearson.com",
        repo="pearson/subscription-svc",
        urn="urn:pearson:service:commerce:subscription-svc",
        produces=[],
        consumes=[
            "commerce.order.placed-value",
            "commerce.payment.processed-value",
            "commerce.order.cancelled-value",
        ],
        app_id="SVC00306",
    ),
    "CI0093447": CmdbCi(
        ci_id="CI0093447",
        name="invoicing-svc",
        domain="finance",
        team_id="TM-FINANCE-PLATFORM",
        team_email="finance-platform@pearson.com",
        repo="pearson/invoicing-svc",
        urn="urn:pearson:service:finance:invoicing-svc",
        produces=[],
        consumes=[
            "commerce.order.placed-value",
            "commerce.payment.processed-value",
            "finance.pricing.changed-value",
        ],
        app_id="SVC01770",
    ),
    "CI0093448": CmdbCi(
        ci_id="CI0093448",
        name="pricing-svc",
        domain="finance",
        team_id="TM-FINANCE-PRICING",
        team_email="finance-pricing@pearson.com",
        repo="pearson/pricing-svc",
        urn="urn:pearson:service:finance:pricing-svc",
        produces=["finance.pricing.changed-value"],
        consumes=["product.product.created-value"],
        app_id="SVC02645",
    ),
    "CI0093449": CmdbCi(
        ci_id="CI0093449",
        name="analytics-svc",
        domain="analytics",
        team_id="TM-ANALYTICS-PLATFORM",
        team_email="analytics-platform@pearson.com",
        repo="pearson/analytics-svc",
        urn="urn:pearson:service:analytics:analytics-svc",
        produces=[],
        consumes=[
            "learning.learner.enrolled-value",
            "learning.progress.updated-value",
            "commerce.order.placed-value",
            "commerce.order.cancelled-value",
        ],
        app_id="SVC01737",
    ),
    # <<< end seed_services.py block

    # The three gold domains whose schemas had nowhere to register (UIFPE-618).
    #
    # register_single_schema resolves service_urn against this dict and refuses
    # the registration when it misses, so product could register on 2026-09-25
    # and price, program and stock could not.
    #
    # `catalog-svc` in every domain, by owner instruction 2026-09-25: the URNs
    # differ only in the domain segment, matching product's. All three run as
    # the same application -- SVC01328, GIP -- which is a real APM id already in
    # _APP_FIXTURES rather than an invented one.
    #
    # THE CI IDS ARE FABRICATED. CI0093421 is a real ServiceNow identifier;
    # these continue the range because a fixture needs a key, and the registry
    # stamps the resolved CI onto every schema as its owner. Real ServiceNow CIs
    # must exist before uat or prod -- see UIFPE-618.
    "CI0093450": CmdbCi(
        ci_id="CI0093450",
        name="catalog-svc",
        domain="price",
        team_id="TM-PRICE-CATALOG",
        team_email="price-catalog@pearson.com",
        repo="pearson-dnt-coe/universal-Integration-framework",
        urn="urn:pearson:service:price:catalog-svc",
        produces=[
            "price.price.gold",
            "price.price.mapping",
        ],
        consumes=[],
        app_id="SVC01328",
    ),
    "CI0093451": CmdbCi(
        ci_id="CI0093451",
        name="catalog-svc",
        domain="program",
        team_id="TM-PROGRAM-CATALOG",
        team_email="program-catalog@pearson.com",
        repo="pearson-dnt-coe/universal-Integration-framework",
        urn="urn:pearson:service:program:catalog-svc",
        produces=[
            "program.program.gold",
            "program.program.mapping",
        ],
        consumes=[],
        app_id="SVC01328",
    ),
    "CI0093452": CmdbCi(
        ci_id="CI0093452",
        name="catalog-svc",
        domain="stock",
        team_id="TM-STOCK-CATALOG",
        team_email="stock-catalog@pearson.com",
        repo="pearson-dnt-coe/universal-Integration-framework",
        urn="urn:pearson:service:stock:catalog-svc",
        produces=[
            "stock.stock.gold",
            "stock.stock.mapping",
            "stock.stock.api-created",
            "stock.stock.api-updated",
        ],
        consumes=[],
        app_id="SVC01328",
    ),
}


class CmdbClient:
    """Interface that real and mock CMDB clients implement."""

    async def get_by_ci_id(self, ci_id: str) -> CmdbCi | None:  # noqa: D401
        raise NotImplementedError

    async def resolve_urn(self, urn: str) -> CmdbCi | None:
        raise NotImplementedError

    async def list_active(self) -> list[CmdbCi]:
        raise NotImplementedError

    async def find_consumers_of(self, artifact_id: str) -> list[CmdbCi]:
        """Return all active CIs whose consumes manifest includes artifact_id."""
        raise NotImplementedError

    async def find_producers_of(self, artifact_id: str) -> list[CmdbCi]:
        """Return all active CIs whose produces manifest includes artifact_id."""
        raise NotImplementedError

    async def update_manifest(self, urn: str, produces: list[str], consumes: list[str]) -> None:
        """Replace the produces/consumes manifest for the CI identified by urn."""
        raise NotImplementedError

    async def get_application(self, app_id: str) -> CmdbApplication | None:
        """Return the APM Application record for a given app_id, or None if unknown."""
        raise NotImplementedError

    async def list_applications(self) -> list[CmdbApplication]:
        """Return all known APM Application records."""
        raise NotImplementedError


class MockCmdbClient(CmdbClient):
    """In-memory CMDB returning hard-coded fixtures. Not for production use."""

    async def get_by_ci_id(self, ci_id: str) -> CmdbCi | None:
        ci = _FIXTURES.get(ci_id)
        if ci is None or not ci.active:
            return None
        return ci

    async def resolve_urn(self, urn: str) -> CmdbCi | None:
        for ci in _FIXTURES.values():
            if ci.urn == urn and ci.active:
                return ci
        return None

    async def list_active(self) -> list[CmdbCi]:
        return [ci for ci in _FIXTURES.values() if ci.active]

    async def find_consumers_of(self, artifact_id: str) -> list[CmdbCi]:
        return [ci for ci in _FIXTURES.values() if ci.active and artifact_id in ci.consumes]

    async def find_producers_of(self, artifact_id: str) -> list[CmdbCi]:
        return [ci for ci in _FIXTURES.values() if ci.active and artifact_id in ci.produces]

    async def update_manifest(self, urn: str, produces: list[str], consumes: list[str]) -> None:
        for ci_id, ci in list(_FIXTURES.items()):
            if ci.urn == urn and ci.active:
                _FIXTURES[ci_id] = CmdbCi(
                    ci_id=ci.ci_id,
                    name=ci.name,
                    domain=ci.domain,
                    team_id=ci.team_id,
                    team_email=ci.team_email,
                    repo=ci.repo,
                    urn=ci.urn,
                    produces=produces,
                    consumes=consumes,
                    active=ci.active,
                    app_id=ci.app_id,
                )
                return

    async def get_application(self, app_id: str) -> CmdbApplication | None:
        return _APP_FIXTURES.get(app_id)

    async def list_applications(self) -> list[CmdbApplication]:
        return list(_APP_FIXTURES.values())


def get_cmdb_client() -> CmdbClient:
    """Factory used by the FastAPI dependency injection layer.

    Always returns the mock. To support a real backend, switch on
    settings.cmdb_base_url and return either MockCmdbClient or a ServiceNow client.
    """
    return MockCmdbClient()
