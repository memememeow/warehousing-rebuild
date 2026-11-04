"""Declare immutable model and colour lookup for front and back SKU pairs.

Lookup keys use trimmed, case-folded product text while accepted orders retain
their display spelling. Construction must reject normalized collisions and
freeze a defensive copy so later input mutation cannot change the catalog.

Constructor and lookup entry/results, normalized keys and resolved SKU pairs
use DEBUG under --debug T. Unknown products retain recoverable rejection
handling; this query owner emits no INFO and stores no debug-mode setting.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from unicodedata import decimal

logger = logging.getLogger("warehouse")


@dataclass(frozen=True, slots=True, init=False)
class ProductCatalog:
    """Provide read-only product translation without allocating order identity."""

    products: Mapping[tuple[str, str], tuple[str, str]]

    def __init__(
        self, products: Mapping[tuple[str, str], tuple[str, str]]
    ) -> None:
        """Validate normalized product keys and retain a frozen defensive copy."""
        logger.debug(
            "function=product_catalog.__init__ action=entry products=%r",
            products, extra={"row_count": len(products)},
        )
        normalized_products: dict[tuple[str, str], tuple[str, str]] = {}
        for supplied_product, skus in products.items():
            normalized_product = _normalize_product(supplied_product, skus)
            if normalized_product in normalized_products:
                reason = (
                    f"Product normalization collision: supplied={supplied_product!r}, "
                    f"normalized={normalized_product!r}"
                )
                logger.debug(
                    "function=product_catalog.__init__ "
                    "action=rejected supplied_product=%r normalized_product=%r reason=%s",
                    supplied_product, normalized_product, reason, extra={"sku_evidence": skus},
                )
                raise ValueError(reason)
            _validate_skus(supplied_product, normalized_product, skus)
            normalized_products[normalized_product] = (skus[0], skus[1])
            logger.debug(
                "function=product_catalog.__init__ "
                "action=validated supplied_product=%r normalized_product=%r",
                supplied_product, normalized_product, extra={"sku_evidence": skus},
            )
        object.__setattr__(self, "products", MappingProxyType(normalized_products))
        logger.debug(
            "function=product_catalog.__init__ action=returned products=%r",
            self.products, extra={"row_count": len(self.products)},
        )

    def resolve_skus(self, model: str, colour: str) -> tuple[str, str]:
        """Resolve the fascia pair case-insensitively or raise ValueError."""
        logger.debug(
            "function=product_catalog.resolve_skus action=entry supplied_product=%r",
            (model, colour),
        )
        normalized_product = (model.strip().casefold(), colour.strip().casefold())
        if normalized_product not in self.products:
            logger.debug(
                "function=product_catalog.resolve_skus "
                "action=rejected supplied_product=%r normalized_product=%r reason=unknown_product",
                (model, colour), normalized_product,
            )
            raise ValueError(
                f"Unknown product: supplied={(model, colour)!r}, normalized={normalized_product!r}"
            )
        skus = self.products[normalized_product]
        logger.debug(
            "function=product_catalog.resolve_skus "
            "action=returned supplied_product=%r normalized_product=%r",
            (model, colour), normalized_product, extra={"sku_evidence": skus},
        )
        return skus


def _normalize_product(
    product: tuple[str, str], skus: tuple[str, str]
) -> tuple[str, str]:
    """Validate a source key before trimming and case-folding its two parts."""
    logger.debug(
        "function=product_catalog._normalize_product action=entry supplied_product=%r",
        product, extra={"sku_evidence": skus},
    )
    if (not isinstance(product, tuple) or len(product) != 2
            or not all(isinstance(part, str) for part in product)):
        reason = f"Product key must be a model/colour string pair: {product!r}"
        logger.debug(
            "function=product_catalog._normalize_product action=rejected "
            "supplied_product=%r normalized_product=n/a reason=%s",
            product, reason, extra={"sku_evidence": skus},
        )
        raise ValueError(reason)
    model, colour = product
    normalized = (model.strip().casefold(), colour.strip().casefold())
    logger.debug(
        "function=product_catalog._normalize_product action=returned "
        "supplied_product=%r normalized_product=%r",
        product, normalized, extra={"sku_evidence": skus},
    )
    return normalized


def _validate_skus(
    product: tuple[str, str], normalized_product: tuple[str, str], skus: tuple[str, str]
) -> None:
    """Require an unchanged front/back tuple of positive decimal SKU strings."""
    logger.debug(
        "function=product_catalog._validate_skus action=entry "
        "supplied_product=%r normalized_product=%r",
        product, normalized_product, extra={"sku_evidence": skus},
    )
    if not isinstance(skus, tuple) or len(skus) != 2:
        reason = f"Product {product!r} requires a front/back SKU tuple: {skus!r}"
        logger.debug(
            "function=product_catalog._validate_skus action=rejected "
            "supplied_product=%r normalized_product=%r reason=%s",
            product, normalized_product, reason, extra={"sku_evidence": skus},
        )
        raise ValueError(reason)
    for side, sku in zip(("front", "back"), skus, strict=True):
        # Inspect decimal digits without parsing or changing the SKU token.
        if (not isinstance(sku, str) or not sku.isdecimal()
                or not any(decimal(digit) for digit in sku)):
            reason = (
                f"Product {product!r} {side} SKU must be a positive decimal string: {sku!r}"
            )
            logger.debug(
                "function=product_catalog._validate_skus action=rejected "
                "supplied_product=%r normalized_product=%r reason=%s",
                product, normalized_product, reason, extra={"sku_evidence": skus},
            )
            raise ValueError(reason)
    logger.debug(
        "function=product_catalog._validate_skus action=returned "
        "supplied_product=%r normalized_product=%r",
        product, normalized_product, extra={"sku_evidence": skus},
    )
