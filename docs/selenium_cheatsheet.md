# Selenium Cheatsheet

## 1. Locate Element

### By ID

driver.find_element(By.ID, "username")

### By Name

driver.find_element(By.NAME, "email")

### By Class Name

driver.find_element(By.CLASS_NAME, "btn-primary")

### By Tag Name

driver.find_element(By.TAG_NAME, "input")

### CSS Selector

driver.find_element(By.CSS_SELECTOR, "#username")
driver.find_element(By.CSS_SELECTOR, ".btn.login")
driver.find_element(By.CSS_SELECTOR, "input\[name='email'\]")

### XPath

driver.find_element(By.XPATH, "//input[@id ='username']")
driver.find_element(By.XPATH, "//div[@class ='item']")
driver.find_element(By.XPATH, "//button\[text()='Login'\]")

------------------------------------------------------------------------

## 2. Dynamic Content (Wait)

### Implicit Wait

driver.implicitly_wait(10)

### Explicit Wait

from selenium.webdriver.support.ui import WebDriverWait from
selenium.webdriver.support import expected_conditions as EC

element = WebDriverWait(driver, 10).until(
EC.presence_of_element_located((By.ID, "username")) )

### Common Conditions

EC.visibility_of_element_located EC.element_to_be_clickable
EC.presence_of_element_located

------------------------------------------------------------------------

## 3. DOM Interaction

### Click

element.click()

### Input

element.send_keys("text") element.clear()

### Scroll

driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
driver.execute_script("arguments\[0\].scrollIntoView();", element)

### Hover

from selenium.webdriver.common.action_chains import ActionChains
ActionChains(driver).move_to_element(element).perform()

### Drag & Drop

ActionChains(driver).drag_and_drop(source, target).perform()

### Dropdown

from selenium.webdriver.support.ui import Select select =
Select(driver.find_element(By.ID, "dropdown"))
select.select_by_visible_text("Option 1")

### iFrame

driver.switch_to.frame("frame_name") driver.switch_to.default_content()

### Alert

alert = driver.switch_to.alert alert.accept()

------------------------------------------------------------------------

## Tips

-   Prefer ID \> CSS \> XPath
-   Use Explicit Wait instead of sleep
-   Use JS when normal actions fail
