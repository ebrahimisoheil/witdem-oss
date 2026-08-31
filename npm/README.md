# Witdem command line launcher

Run the Witdem receiver, ELT worker, and dashboard without cloning the repository:

```bash
npx -y witdem@0.1.1 up
```

This lightweight package calls Docker Compose with the matching, pinned Witdem container image. It does not install a daemon, modify your application, or run scripts during npm installation.

The dashboard opens at `http://localhost:8501`, and the OTLP/SDK receiver listens at `http://localhost:4318`. Collected data remains in the `witdem-data` Docker volume across restarts and upgrades.

```bash
npx -y witdem@0.1.1 status
npx -y witdem@0.1.1 logs
npx -y witdem@0.1.1 down
```

Run `npx -y witdem@0.1.1 --help` for port and image overrides. Docker with the Compose plugin is the only runtime prerequisite. Docker reuses a matching local image and pulls it when missing.
