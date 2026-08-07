You are acting as a Senior Software Architect and Code Reviewer.

Your task is to understand the entire project before making any changes.

IMPORTANT RULES:

* Do NOT modify, refactor, delete, rename, or create any files.
* Do NOT suggest code changes yet unless I explicitly ask.
* Do NOT generate patches or implementation code.
* Treat the project as READ-ONLY.
* Your current goal is only to understand and document the project.

Please analyze the complete codebase and provide:

1. Project Overview

   * What this project does
   * Main business purpose
   * Core modules/features

2. Technology Stack

   * Frameworks
   * Languages
   * Libraries
   * External services/APIs
   * Database technologies

3. Folder & File Structure

   * Explain important directories
   * Explain responsibilities of major files

4. Application Flow

   * Request lifecycle
   * Authentication flow
   * User flow
   * Data flow

5. Database Analysis

   * Tables used
   * Relationships
   * Key business entities
   * Important models

6. Architecture Analysis

   * Controllers
   * Services
   * Components
   * Helpers
   * Models
   * Views
   * Middleware/Filters

7. Dependency Mapping

   * Which modules depend on which modules
   * Critical files that affect multiple areas

8. Feature Inventory

   * List every major feature discovered
   * Explain how each feature works

9. API & Integration Analysis

   * External APIs
   * Webhooks
   * Payment gateways
   * Third-party integrations

10. Security & Permissions

* User roles
* Access control
* Authentication mechanisms

11. Development Risks

* Areas that are tightly coupled
* Files that should be modified carefully
* Potential impact zones

12. Knowledge Base
    Create a detailed project documentation that can be used as context for future development tasks.

Output Format:

* Use clear headings.
* Reference actual file paths.
* Mention assumptions separately.
* If information is missing, say "Not enough evidence found".

Before giving conclusions, scan the entire codebase and build a complete understanding of the project.

DO NOT CHANGE ANYTHING.
DO NOT WRITE CODE.
DO NOT GENERATE PATCHES.
ONLY ANALYZE AND DOCUMENT.