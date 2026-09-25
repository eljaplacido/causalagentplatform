"""The package version. pyproject.toml reads it from here (hatch dynamic version).

Not read from installed metadata: an editable install records the version at
install time, so a later bump left the running code reporting the old one --
and causal-core hashes its version into every result's provenance.
"""

__version__ = "0.4.0"
