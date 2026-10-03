"""Core services. Every service method that touches college data takes ``college_id``
explicitly, and no method spans colleges (R7, design "Isolation in the core")."""
