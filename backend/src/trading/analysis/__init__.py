"""Analysis run plumbing: graph loading, output validation and decision policy (§7).

Pure functions only. Provider calls, persistence and budgets live in workers
added in later phases; nothing here talks to a model or an exchange.
"""
