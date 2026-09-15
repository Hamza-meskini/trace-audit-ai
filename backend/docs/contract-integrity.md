# Production contract integrity

The production path uses the same extraction, predicate validation and verdict
aggregation services regardless of document vocabulary or requirement IDs.

## Source boundaries

Document preambles remain available during discovery. Heading identification
handles preambles, and construction starts at the identified requirement heading.
If an identified document contains no matching requirement or parent clause, the
discovered description is used provisionally and the result requires source
review. Logic-cue validation examines the extracted semantic clause spans, not
unrelated prose elsewhere in the section.

## Predicates

New extractions restrict comparisons to `<`, `<=`, `==`, `!=`, `>=`, `>`,
`between`, `in`, and `not_in`. Qualitative predicates carry an expected boolean
or state; actions belong in the description and parameter, not in the operator.
Unambiguous aliases are normalized by the shared predicate module. Missing or
incompatible operands and reversed intervals prevent contract completeness.

For a comparison between two quantities, `right_operand` contains the source
wording of the second quantity. Literal thresholds and bounds must be absent.
This field is preserved through production contract conversion and shown to the
verifier. It does not imply that numeric evidence for both operands is available.

Legacy contracts can still deserialize unsupported operator strings so a single
old record does not crash a project. Execution validation flags them as blocking
predicate errors. Legacy prose-only conditions remain supported; new extraction
validation requires explicit comparisons.

## Graph compilation and coverage

A reference to a known condition without an explicit graph node is materialized
as a leaf before condition IDs are reassigned. Explicit node IDs take priority.
Unknown references and cycles remain invalid drafts; validation prevents them
from authoritatively determining a verdict. Their diagnostic representation is
retained for repair and review.

Coverage is rebuilt from condition-to-clause links. Missing IDs in redundant
coverage entries are resolved by unique source text or existing valid links.
Explicit unknown IDs remain errors. Duplicate detection includes the grounded
action wording, so equal subjects and comparison values alone are insufficient.

## Verdict validation

Live findings carry a code, path and blocking flag while retaining string
compatibility with existing reports. Nested structure errors are always blocking.
Stored strings are not the authority for execution: the current contract is
validated again.

For a complete contract with no other validation findings, aggregation can use a
sufficient proof consisting entirely of evidence marked `VALID`:

- `ALL_OF`: one applicable failed obligation proves conflict; support needs all.
- `ANY_OF`: one complete supported path proves support; conflict needs all active paths.
- `IF_THEN`: the antecedent needs validated applicability before using its consequent.

The sufficient proof must agree with the ordinary atomic aggregate. Unresolved
evidence on other branches remains in diagnostics; statuses and validation
states are not rewritten to manufacture proof. Incomplete or ambiguous contracts
cannot use this exception. Existing review policy still applies.

## Regression coverage

`evaluation/tests/test_production_contract_integrity.py` exercises invented
requirements, source isolation, operator and operand validation, coverage repair,
ID reassignment, nested invalid trees, cycles, distinct actions, decisive proofs,
unresolved applicability, legacy records and finalizer behavior without model calls.

Previously stored extractions are not rewritten by these changes. Re-extract
documents to exercise the updated construction rules. Live model accuracy and
retry cost require a fresh inference run; unit tests do not estimate those metrics.
