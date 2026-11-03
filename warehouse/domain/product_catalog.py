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
            "function=warehouse.domain.product_catalog.ProductCatalog.__init__ "
            "action=entry supplied_products=%r normalized_products=n/a",
            products, extra={"row_count": len(products)},
        )
        normalized_products: dict[tuple[str, str], tuple[str, str]] = {}
        for supplied_product, skus in products.items():
            normalized_product: tuple[str, str] | None = None
            try:
                if (not isinstance(supplied_product, tuple) or len(supplied_product) != 2
                        or not all(isinstance(part, str) for part in supplied_product)):
                    raise ValueError(
                        f"Product key must be a model/colour string pair: {supplied_product!r}"
                    )
                model, colour = supplied_product
                normalized_product = (model.strip().casefold(), colour.strip().casefold())
                if normalized_product in normalized_products:
                    raise ValueError(
                        f"Product normalization collision: supplied={supplied_product!r}, "
                        f"normalized={normalized_product!r}"
                    )
                if not isinstance(skus, tuple) or len(skus) != 2:
                    raise ValueError(
                        f"Product {supplied_product!r} requires a front/back SKU tuple: {skus!r}"
                    )
                for side, sku in zip(("front", "back"), skus, strict=True):
                    # Inspect decimal digits without parsing or changing the SKU token.
                    if (not isinstance(sku, str) or not sku.isdecimal()
                            or not any(decimal(digit) for digit in sku)):
                        raise ValueError(
                            f"Product {supplied_product!r} {side} SKU must be a "
                            f"positive decimal string: {sku!r}"
                        )
            except ValueError as error:
                logger.debug(
                    "function=warehouse.domain.product_catalog.ProductCatalog.__init__ "
                    "action=rejected supplied_product=%r normalized_product=%r reason=%s",
                    supplied_product, normalized_product if normalized_product is not None else "n/a",
                    error, extra={"sku_evidence": skus},
                )
                raise
            normalized_products[normalized_product] = (skus[0], skus[1])
            logger.debug(
                "function=warehouse.domain.product_catalog.ProductCatalog.__init__ "
                "action=validated supplied_product=%r normalized_product=%r",
                supplied_product, normalized_product, extra={"sku_evidence": skus},
            )
        object.__setattr__(self, "products", MappingProxyType(normalized_products))
        logger.debug(
            "function=warehouse.domain.product_catalog.ProductCatalog.__init__ "
            "action=returned normalized_products=%r",
            self.products, extra={"row_count": len(self.products)},
        )

    def resolve_skus(self, model: str, colour: str) -> tuple[str, str]:
        """Resolve the fascia pair case-insensitively or raise ValueError."""
        logger.debug(
            "function=warehouse.domain.product_catalog.ProductCatalog.resolve_skus "
            "action=entry supplied_product=%r normalized_product=n/a",
            (model, colour),
        )
        normalized_product = (model.strip().casefold(), colour.strip().casefold())
        try:
            skus = self.products[normalized_product]
        except KeyError:
            logger.debug(
                "function=warehouse.domain.product_catalog.ProductCatalog.resolve_skus "
                "action=rejected supplied_product=%r normalized_product=%r reason=unknown_product",
                (model, colour), normalized_product,
            )
            raise ValueError(
                f"Unknown product: supplied={(model, colour)!r}, normalized={normalized_product!r}"
            ) from None
        logger.debug(
            "function=warehouse.domain.product_catalog.ProductCatalog.resolve_skus "
            "action=returned supplied_product=%r normalized_product=%r",
            (model, colour), normalized_product, extra={"sku_evidence": skus},
        )
        return skus
