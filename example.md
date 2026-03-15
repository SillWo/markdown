# Демонстрация Markdown to PDF конвертера

## Введение

Это демонстрационный файл, показывающий возможности конвертера Markdown в PDF с поддержкой диаграмм Mermaid.

## Блок-схема: Процесс заказа

```mermaid
flowchart TD
    A[Начало: Клиент на сайте] --> B{Товар в наличии?}
    B -->|Да| C[Добавить в корзину]
    B -->|Нет| D[Уведомить о поступлении]
    C --> E[Оформление заказа]
    E --> F{Выбор оплаты}
    F -->|Карта| G[Онлайн оплата]
    F -->|Наличные| H[Оплата курьеру]
    G --> I[Подтверждение оплаты]
    H --> I
    I --> J[Отправка заказа]
    J --> K[Доставка]
    K --> L[Получение товара]
    L --> M[Конец]
    D --> N[Ожидание]
    N --> A
```

## Диаграмма последовательности: Аутентификация

Процесс входа пользователя в систему:

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant B as Браузер
    participant S as Сервер
    participant DB as База данных

    U->>B: Ввод логина/пароля
    B->>S: POST /auth/login
    S->>DB: SELECT user WHERE email=?
    DB-->>S: user_data
    S->>S: Проверка пароля (bcrypt)
    alt Успешная аутентификация
        S->>S: Создание JWT токена
        S-->>B: 200 OK + token
        B->>B: Сохранить token в localStorage
        B-->>U: Успешный вход
    else Ошибка аутентификации
        S-->>B: 401 Unauthorized
        B-->>U: Ошибка входа
    end
```

## Таблица: Сравнение технологий

| Технология | Скорость | Масштабируемость | Сложность | Рейтинг |
|-----------|----------|-----------------|-----------|---------|
| FastAPI | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | 9/10 |
| Django | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 8/10 |
| Flask | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | 7/10 |
| Express.js | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | 8/10 |

## Диаграмма классов: Система управления

```mermaid
classDiagram
    class User {
        +int id
        +string email
        +string name
        +datetime created_at
        +login()
        +logout()
        +updateProfile()
    }

    class Order {
        +int id
        +float total_amount
        +string status
        +datetime created_at
        +calculate_total()
        +update_status()
        +cancel()
    }

    class Product {
        +int id
        +string name
        +float price
        +int stock
        +update_stock()
        +check_availability()
    }

    class OrderItem {
        +int id
        +int quantity
        +float price_at_order
        +calculate_subtotal()
    }

    User "1" --> "*" Order : places
    Order "1" --> "*" OrderItem : contains
    Product "1" --> "*" OrderItem : referenced in
```

## График Ганта: План проекта

```mermaid
gantt
    title Разработка веб-приложения
    dateFormat  YYYY-MM-DD
    section Анализ
    Сбор требований           :done,    req1, 2024-01-01, 2024-01-10
    Проектирование архитектуры :done,   req2, 2024-01-08, 2024-01-20
    section Дизайн
    Прототипирование          :active,  des1, 2024-01-15, 2024-02-01
    UI/UX дизайн              :         des2, 2024-01-25, 2024-02-15
    section Разработка
    Backend API               :         dev1, 2024-02-01, 2024-03-15
    Frontend разработка       :         dev2, 2024-02-10, 2024-03-20
    Интеграция компонентов    :         dev3, 2024-03-15, 2024-04-01
    section Тестирование
    Модульное тестирование    :         test1, 2024-03-01, 2024-03-25
    Интеграционное тестирование:        test2, 2024-03-25, 2024-04-10
    section Деплой
    Настройка инфраструктуры  :         dep1, 2024-04-01, 2024-04-08
    Релиз в продакшен         :crit,    dep2, 2024-04-10, 2024-04-12
```

## Примеры кода

### Python: FastAPI endpoint

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List

app = FastAPI()

class User(BaseModel):
    id: int
    email: str
    name: str

@app.get("/api/users", response_model=List[User])
async def get_users(skip: int = 0, limit: int = 100):
    """
    Получить список пользователей с пагинацией
    """
    users = await db.users.find().skip(skip).limit(limit).to_list()
    return users

@app.post("/api/users", response_model=User, status_code=201)
async def create_user(user: User):
    """
    Создать нового пользователя
    """
    if await db.users.find_one({"email": user.email}):
        raise HTTPException(status_code=400, detail="Email already exists")

    result = await db.users.insert_one(user.dict())
    user.id = result.inserted_id
    return user
```

### JavaScript: React компонент

```javascript
import React, { useState, useEffect } from 'react';
import axios from 'axios';

function UserList() {
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchUsers = async () => {
            try {
                const response = await axios.get('/api/users');
                setUsers(response.data);
            } catch (error) {
                console.error('Error fetching users:', error);
            } finally {
                setLoading(false);
            }
        };

        fetchUsers();
    }, []);

    if (loading) return <div>Loading...</div>;

    return (
        <div className="user-list">
            <h2>Список пользователей</h2>
            <ul>
                {users.map(user => (
                    <li key={user.id}>
                        {user.name} ({user.email})
                    </li>
                ))}
            </ul>
        </div>
    );
}

export default UserList;
```

## Круговая диаграмма: Распределение задач

```mermaid
pie title Распределение времени разработки
    "Backend разработка" : 35
    "Frontend разработка" : 30
    "Тестирование" : 20
    "DevOps и деплой" : 10
    "Документация" : 5
```

## Цитаты и заметки

> **Важно:** При работе с асинхронным кодом всегда используйте `async`/`await` для предотвращения блокировки event loop.

> **Совет:** Применяйте кэширование для часто запрашиваемых данных. Это может снизить нагрузку на базу данных на 70-80%.

## Списки

### Преимущества микросервисной архитектуры:

1. **Независимое развертывание** - каждый сервис можно обновлять отдельно
2. **Масштабируемость** - масштабирование только нужных компонентов
3. **Технологическая гибкость** - разные языки для разных сервисов
4. **Отказоустойчивость** - изоляция сбоев

### Недостатки:

- Сложность управления множеством сервисов
- Необходимость в service discovery
- Усложнение мониторинга и отладки
- Распределенные транзакции

## Диаграмма состояний: Жизненный цикл заказа

```mermaid
stateDiagram-v2
    [*] --> Created: Создан
    Created --> Pending: Ожидание оплаты
    Pending --> Paid: Оплачен
    Pending --> Cancelled: Отменен (тайм-аут)
    Paid --> Processing: В обработке
    Processing --> Shipped: Отправлен
    Shipped --> Delivered: Доставлен
    Delivered --> Completed: Завершен

    Paid --> Refunding: Возврат
    Processing --> Refunding
    Refunding --> Refunded: Возвращен

    Created --> Cancelled: Отменен клиентом
    Paid --> Cancelled: Отменен
    Processing --> Cancelled: Отменен

    Completed --> [*]
    Cancelled --> [*]
    Refunded --> [*]
```

## Заключение

Этот конвертер позволяет создавать профессиональные PDF-документы из Markdown с полной поддержкой:

- ✅ Сложных диаграмм Mermaid
- ✅ Таблиц с форматированием
- ✅ Подсветки синтаксиса кода
- ✅ Автоматического масштабирования диаграмм
- ✅ Красивого оформления

**Все диаграммы автоматически масштабируются и никогда не разрываются на несколько страниц!**
