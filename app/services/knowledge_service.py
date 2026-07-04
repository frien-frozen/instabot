"""Combine knowledge base records into AI context."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import Knowledge
from app.utils.logging import get_logger

logger = get_logger(__name__)

CATEGORY_LABELS = {
    "about": "About",
    "faq": "FAQ",
    "products": "Products",
    "pricing": "Pricing",
    "website": "Website",
    "links": "Links",
    "contact": "Contact",
    "custom": "Notes",
}


class KnowledgeService:
    """Load and format knowledge for prompt injection."""

    async def build_context(self, session: AsyncSession, account_id: int) -> str | None:
        result = await session.execute(
            select(Knowledge)
            .where(Knowledge.account_id == account_id, Knowledge.is_active.is_(True))
            .order_by(Knowledge.sort_order, Knowledge.id)
        )
        items = result.scalars().all()
        if not items:
            return None

        sections: list[str] = []
        for item in items:
            label = CATEGORY_LABELS.get(item.category, item.category.title())
            sections.append(f"## {label}: {item.title}\n{item.content}")

        return "Knowledge base:\n\n" + "\n\n".join(sections)
