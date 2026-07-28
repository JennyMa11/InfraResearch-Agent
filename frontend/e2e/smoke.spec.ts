import { expect, test } from "@playwright/test";

test("research workspace renders", async ({ page }) => {
  await page.route("**/api/v1/sources", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.goto("/");
  await expect(page.getByText("找到可验证的答案")).toBeVisible();
  await expect(page.getByLabel("研究问题")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Agent 路径" })).toBeVisible();
});
