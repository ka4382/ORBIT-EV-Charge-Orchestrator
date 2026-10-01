Feature: Cost-optimal charging plan
  As a fleet manager I want a charging schedule that reaches the target SoC before departure
  at the lowest tariff cost so that energy spend drops without stranding vehicles.

  Scenario: Plan an overnight charge under a time-of-use tariff
    Given a vehicle in my tenant at 30% state of charge
    When I request a plan to reach 90% by 06:00 tomorrow
    Then the plan becomes READY within 30 seconds
    And every slot is inside the off-peak tariff window
    And the plan cost is lower than the charge-on-arrival baseline

  Scenario: Infeasible target is reported, not silently truncated
    Given a vehicle in my tenant at 10% state of charge
    When I request a plan to reach 100% in 15 minutes
    Then the plan status is FAILED or the plan is flagged infeasible
