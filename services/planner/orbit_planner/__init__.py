"""ORBIT planner: charging-plan computation (Dijkstra routing + DP tariff schedule + depot smoothing).

Layering (hexagonal):
  domain/          pure algorithms, no IO
  application/     use cases over ports (Protocols)
  infrastructure/  Postgres, Redis and Kafka adapters
  __main__         Kafka consumer loop and `--nightly` CLI
"""
