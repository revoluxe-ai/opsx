# Шаблон одного файла scenarios/<sha256(scenario_id)>.md
#
# Имя файла: sha256(UTF-8 bytes of full SCENARIO_ID string).hexdigest() + ".md"
# SCENARIO_ID format: openspec/changes/<CHANGE_ID>/specs/.../spec.md::<Scenario title>

    - SCENARIO_ID: openspec/changes/<CHANGE_ID>/specs/<spec-dir>/spec.md::<Scenario title>
      LIFECYCLE_STEP: L1 | L2 | L3 | L4 | L5 | L6 | L7 | L_cross
      GIVEN_WHEN_THEN: <one line GIVEN/WHEN/THEN from spec delta>
      PRODUCTION_ENTRYPOINT: <module / handler / seam>
      CAUSAL_PATH: <how evidence links spec behavior to code boundary>
      REQUIRED_EVIDENCE:
        - scenario_seam
        - unit_component
        # or contract_cases, stub_contract, boundary_integration_*, etc. per plan matrix
      NEGATIVE_ASSERTIONS: <optional forbidden outcomes>
      TESTS_EXIST: not_claimed
      EXECUTION: NOT_EXECUTED
