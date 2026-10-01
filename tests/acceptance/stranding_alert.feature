Feature: Stranding-risk alert
  As a fleet manager I want to be told within seconds when a vehicle cannot reach any charger
  so that I can dispatch help before it stops on the road.

  # @wip: needs a deterministic strand injection in the simulator (SIM_SCENARIO=strand), not built yet.
  # The rule itself is covered by unit tests and the Testcontainers alert integration test.
  @wip
  Scenario: A vehicle below 15% SoC with no reachable charger raises a CRITICAL alert
    Given a vehicle in my tenant
    When its telemetry reports 12% state of charge far from any charger
    Then a STRAND_RISK alert with severity CRITICAL appears in the alert feed within 5 seconds
    And the alert can be acknowledged
    And an audit record of the acknowledgement exists
